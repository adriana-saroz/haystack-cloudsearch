import logging
import time
from datetime import datetime

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db.models.loading import get_model
from django.template.defaultfilters import iriencode

import haystack
from haystack.backends import BaseEngine, BaseSearchBackend, BaseSearchQuery
from haystack.exceptions import MissingDependency
from haystack.models import SearchResult
from haystack.utils import get_identifier

from haystack_cloudsearch.cloudsearch_utils import get_domain
from haystack_cloudsearch.exceptions import *

from haystack_cloudsearch.cloudsearch_utils import (ID, DJANGO_CT, DJANGO_ID,
                                                    gen_version,
                                                    botobool)
from haystack_cloudsearch.fields import LiteralField

try:
    import boto
except ImportError:
    raise MissingDependency("The 'cloudsearch' backend requires the installation of 'boto'. Please refer to the documentation.")

cache = {}

class CloudsearchSearchBackend(BaseSearchBackend):

    def __init__(self, connection_alias, **connection_options):
        super(CloudsearchSearchBackend, self).__init__(connection_alias, **connection_options)

        if not 'AWS_ACCESS_KEY_ID' in connection_options:
            raise ImproperlyConfigured("You must specify a 'AWS_ACCESS_KEY_ID' in your settings for connection '%s'." % connection_alias)

        if not 'AWS_SECRET_KEY' in connection_options:
            raise ImproperlyConfigured("You must specify a 'AWS_SECRET_KEY' in your settings for connection '%s'." % connection_alias)

        # Allow overrides for the SearchDomain prefix
        self.search_domain_prefix = connection_options.get('SEARCH_DOMAIN_PREFIX', 'haystack')

        # Setup the maximum amount of time to spin while waiting
        self.max_spin_cycle = connection_options.get('MAX_SPINLOCK_TIME', 60 * 60)

        self.prepare_silently = connection_options.get('PREPARE_SILENTLY', False)

        self.ip_address = connection_options.get('IP_ADDRESS')
        if self.ip_address is None:
            raise ImproperlyConfigured("You must specify IP_ADDRESS in your settings for connection '%s'." % connection_alias)

        self.boto_conn = boto.connect_cloudsearch2(connection_options['AWS_ACCESS_KEY_ID'], connection_options['AWS_SECRET_KEY'])
        # this will become a standard haystack logger down the line
        self.log = logging.getLogger('haystack-cloudsearch')
        self.setup_complete = False

    def get_domain(self, index):
        """ Given a SearchIndex, return a boto Domain object """
        domain = self.get_searchdomain_name(index)
        try:
            return cache['domain']
        except KeyError:
            result = get_domain(domain, self.boto_conn)
            cache['domain'] = result
            return result

    def enable_index_access(self, index, ip_address):
        """ given an index and an ip_address to enable, enable searching and document services """
        return self.enable_domain_access(self.get_searchdomain_name(index), ip_address)

    def enable_domain_access(self, search_domain, ip_address):
        """ takes the cloudsearch search_domain name  and an ip_address to enable searching and doc services for
        """
        domain = get_domain(search_domain, self.boto_conn)
        if domain is None:
            raise Exception('Unable to enable SearchDomain %s because %s was not found.' % (search_domain, search_domain))
        policy = domain.get_access_policies()
        r0 = policy.allow_search_ip(ip_address)
        r1 = policy.allow_doc_ip(ip_address)
        return r0, r1

    def get_searchdomain_name(self, index, cache={}):
        """
        Given a SearchIndex, calculate the name for the CloudSearch SearchDomain

        """
        try:
            return cache[index]
        except KeyError:
            model = index.get_model()
            name = getattr(getattr(index, 'Meta', object()), 'index_name', None)
            if name is not None:
                cache[index] = '%s-%s' % (self.search_domain_prefix, name)
            else:
                cache[index] = "%s-%s-%s" % tuple(map(lambda x: x.lower(), (self.search_domain_prefix, model._meta.app_label, unicode(index.__class__.__name__).strip('_'))))
            return cache[index]

    def validate_search_domain_name(self, search_domain_name):
        """
        Validates a SearchDomain name generated from an index against Amazon Cloudsearch constraints.

        """
        return len(search_domain_name) <= 28

    def setup(self):
        """
        Create a cloudsearch schema based on haystack SearchIndexes if
        the haystack models don't match what exists in cloudsearch.

        """
        haystack_conn = haystack.connections[self.connection_alias]
        unified_index = haystack_conn.get_unified_index()

        for index in unified_index.collect_indexes():

            search_domain_name = self.get_searchdomain_name(index)
            try:
                self.validate_search_domain_name(search_domain_name)
            except ValidationError:
                self.log.critical("Generated SearchDomain name, '%s', for index, '%s', failed validation constraints." % (
                    search_domain_name, index))
                raise

            domain = self.boto_conn.lookup(search_domain_name)
            if domain is None:
                domain = self.boto_conn.create_domain(search_domain_name)
                self.setup_complete = False

            all_fields = self.get_haystack_fields()
            all_fields.update(index.fields)

            cloud_schema = domain.get_index_fields()

            for field_name, field in all_fields.items():

                cloud_field = next((item for item in cloud_schema if item["IndexFieldName"] == field_name), None)
                if cloud_field != field.get_index_schema():

                    domain.create_index_field (
                        field_name = field.index_fieldname,
                        field_type = field.field_type,
                        default = field._default,
                        facet = field.faceted,
                        returnable = field.return_enabled,
                        searchable = field.search_enabled,
                        highlight = field.highlight_enabled,
                        source_field = field.source_field,
                        analysis_scheme = field.analysis_scheme
                    )

        self.setup_complete = True  # should be True when finished

    def validate_index_field_name(self, name):
        """ validation checks for index field name requirements imposed by Amazon Cloudsearch """
        return True

    def get_haystack_fields(self):
        fields = {}
        for field_name in (DJANGO_ID, DJANGO_CT):
            field = LiteralField()
            field.set_instance_name(field_name)
            fields[field_name] = field
        return fields

    def get_index_for_obj(self, obj):
        """ inefficiently resolves obj into an index that obj is part of. this could
            return unexpected results if you have more than one index on a given model...

            returns None on failure
        """
        return self.get_model_to_index_map().get(obj.__class__.__name__, None)

    def get_model_to_index_map(self):
        """ returns a dict mapping django model class names to SearchIndexes

            WARNING!!!!
            This can have adverse results if you have more than one SearchIndex mapped
            to the same model...

            This bakes in an assumption that seemingly the rest of haystack makes
            about one ORM Model per Haystack SearchIndex. This will need to change
            when the assumption is lifted.
        """
        haystack_conn = haystack.connections[self.connection_alias]
        unified_index = haystack_conn.get_unified_index()
        return dict((index.get_model().__name__, index)
                    for index in unified_index.collect_indexes())

    def update(self, index, iterable, errors_allowed=False):
        iterable = list(iterable)
        if not self.setup_complete:
            try:
                self.setup()
            # we need to map which exceptions are possible here and handle them appropriately
            except Exception, e:
                self.log.error(u'Failed to add documents to Cloudsearch')
                name = getattr(e, '__name__', e.__class__.__name__)
                self.log.error(u'%s while setting up index' % name, exc_info=True,
                               extra={'data': {'index': index}})
                if not self.prepare_silently:
                    raise
                return

        doc_service = self.get_domain(index).get_document_service()

        prepped_objs = []
        for obj in iterable:
            try:
                prepped_objs.append(index.full_prepare(obj))

            # we need to map which exceptions are possible here and handle them appropriately
            except Exception, e:
                name = getattr(e, '__name__', e.__class__.__name__)
                self.log.error(u'%s while preparing object for update' % name, exc_info=True, extra={'data': {'index': index, 'object': get_identifier(obj)}})
                if not self.prepare_silently:
                    raise

        # extra sanity checking on demand
        if not errors_allowed:
            if len(prepped_objs) != len(iterable):
                raise ValidationError('Number of objects successully prepared differs from number presented for update')

        # this needs some help in terms of generating an id
        for obj in prepped_objs:
            obj['id'] = obj['id'].replace('.', '__')
            doc_service.add(obj['id'], obj)
        # this can fail if the upload is too large;
        # there should be some error handling around this
        doc_service.commit()

    def remove(self, obj_or_string):
        """ accepts a haystack id such as APP_LABEL.MODEL.PK
            OR a model instance
        """
        if isinstance(obj_or_string, basestring):
            app_label, model_name, pk = obj_or_string.split('.')
            obj_id = u"%s__%s__%s" % (app_label, model_name, pk)
            index = get_model(app_label, model_name)
        else:
            obj_id = u"%s__%s__%s" % (obj_or_string._meta.app_label, obj_or_string._meta.module_name, obj_or_string._get_pk_val())
            index = self.get_index_for_obj(obj_or_string)

        doc_service = self.get_domain(index).get_document_service()
        doc_service.delete(obj_id, int(time.mktime(datetime.utcnow().timetuple())))
        doc_service.commit()

    def index_event(self, index):
        """ cause reindexing of a particular index """
        return self.boto_conn.layer1.index_documents(self.get_searchdomain_name(index))

    def clear(self, models=None, commit=True, domains=None, indexes=None, everything=False, spinlock=True):
        """ clear SearchDomains by model, index, or everything """
        # the implementation here just deletes the domain, recreates it, then reloads the schema
        domains = domains or []
        if models is not None:
            m = self.get_model_to_index_map()
            for model in models:
                domains.append(self.get_searchdomain_name(m[model.__class__.__name__]))

        if indexes is not None:
            for i in indexes:
                domains.append(self.get_searchdomain_name(i))

        if models is None and indexes is None and not domains:
            domains = [x['domain_name'] for x in self.boto_conn.layer1.describe_domains()]
            if not everything:
                conn = haystack.connections[self.connection_alias]
                unified_index = conn.get_unified_index()
                index_set = set([self.get_searchdomain_name(i) for i in unified_index.collect_indexes()])
                domains = list(set(domains) & index_set)

        self.log.debug('deleting domains: %s' % (', '.join(domains),))
        for d in domains:
            self.boto_conn.layer1.delete_domain(d)

        if spinlock:
            if self.domain_processing_spinlock(domains):
                if commit:
                    self.setup()
            else:
                raise CloudsearchDryerExploded('While waiting for a delete domain to finish, we hit our max timeout. Please investigate your SearchDomains.')
        else:
            if commit:
                self.setup()

    def spinlock(self, test, exception, description):
        """ execute test, spinning on exception, returning True if the test passes """
        self.log.debug('entering %s spinlock' % (description,))
        t0 = int(time.time())
        while (int(time.time()) - t0) < self.max_spin_cycle:
            try:
                if test():
                    self.log.debug('leaving %s spinlock' % (description,))
                    return True
                self.log.debug('no exception, sleeping during %s spinlock' % (description,))
                time.sleep(60)
            except exception:
                self.log.debug('exception sleeping during %s spinlock' % (description,))
                time.sleep(60)
        return False

    def domain_processing_spinlock(self, domains):
        return self.spinlock(lambda: not filter(None, map(get_domain, domains, self.boto_conn)), CloudsearchProcessingException, 'domain processing')

    def search(self, query_string, **kwargs):
        """ Blended search across all SearchIndexes.

            limit_indexes - list of indexes to limit the search to (default: search all registered indexes)

            query_string should typically be in the format of:

                ($OPERATOR (label $SOMEFIELD:"param") (label $SOMEFIELD:"param"))

            where $OPERATOR is either 'and', 'or', or 'not' and $SOMEFIELD is a field name in the SearchDomain
            The param passed can be prefix matching by including an * (asterisk) after your prefix.
            Unsigned integers can be specified with multiple numbers comma-separated and with ranges such that
            ..NUMBER is -infinity to NUMBER
            NUMBER0..NUMBER1 is from NUMBER0 to NUMBER1
            NUMBER.. is from NUMBER to +infinity

            See cloudsearch documentation for more information.

            facet - is a list of facet field names.
            facet-top-n is a dict of facet field names to an integer specifying how many facets to return
                e.g. facet-top-n={'my-faceted-field': 5} (default is 10)
            facet-constraints is a dict of facet field names to constraints as described by the cloudsearch docs. e.g.
                facet-constraints={'my-faceted-field': ['blue'], 'my-other-facteted-field': '1999..2010'} (default: no constraints)

            :raises: boto.cloudsearch.CloudsearchProcessingException, boto.cloudsearch.CloudsearchNeedsIndexingException
        """

        # by convention, empty query strings return no results
        if len(query_string) == 0:
            return {'results': [],
                    'hits': 0,
                    'facets': {}}

        #if not self.setup_complete:
        #    self.setup()

        try:
            indexes = kwargs.pop('limit_indexes')
        except:
            indexes = None

        if indexes is None:
            conn = haystack.connections[self.connection_alias]
            unified_index = conn.get_unified_index()
            indexes = unified_index.collect_indexes()

        results = []
        for index in indexes:
            results.append(self._process_results(self.search_index(index, query_string, **kwargs)))

        total_hits = 0
        facets = {}
        total_results = []
        for r in results:
            # this moves the synthetic scores we already know to be sketchy into the realm of completely meaningless
            total_results.extend(r['results'])
            total_hits += r['hits']
            # this will mangle some results if you have facets from two search domains with the same index field name...
            facets.update(r['facets'])

        return {
            'results': total_results,
            'hits': total_hits,
            'facets': facets,
        }

    def field_names_for_index(self, index):
        return [x.index_fieldname for x in index.fields]

    def internal_field_names(self):
        # this shouldn't be hardcoded and thus is a function for now
        return [u'django_id', u'id', u'djang_ct']

    def search_index(self, index, query_string, **kwargs):
        """ given an index and a boolean query, return raw boto results

        :raises: boto.cloudsearch.CloudsearchProcessingException, boto.cloudsearch.CloudsearchNeedsIndexingException
        """
        try:
            return_fields = [kwargs.pop('return_fields')]
            return_fields.extend(self.internal_field_names())
            return_fields = list(set(return_fields))
        except KeyError:
            return_fields = self.field_names_for_index(index)
        try:
            search_service = self.get_domain(index).get_search_service()
        except (CloudsearchProcessingException, CloudsearchNeedsIndexingException):
            raise  # We should probably wrap this into something more common to haystack
        query = search_service.search(bq="text:'%s'" % iriencode(query_string),
                                      return_fields=return_fields,
                                      start=kwargs.get('start_offset', 0),
                                      size=kwargs.get('end_offset', 0),)
        return query

    def _process_results(self, boto_results, result_class=None):
        """ return a dict compatible with SearchQuerySet when given raw boto results
            cloudsearch doesn't really provide a scoring mechanism, so we use reverse
            rank as a score
        """
        results = []
        hits = boto_results.hits
        facets = {}
        if result_class is None:
            result_class = SearchResult

        if hasattr(boto_results, 'facets'):
            facets = {'fields': {},
                      'dates': {},
                      'queries': {}}

            for facet_fieldname, individuals in boto_results.facets.items():
                facets['fields'][facet_fieldname] = [(x[u'value'], x[u'count']) for x in individuals[u'constraints']]

        unified_index = haystack.connections[self.connection_alias].get_unified_index()
        indexed_models = unified_index.get_indexed_models()

        # this isn't really a score, just the ranking, but it's the best we get
        # out of cloudsearch
        offset = 0 if boto_results.query.start is None else boto_results.query.start
        for weight, result in enumerate([x['data'] for x in boto_results.docs], offset):
            app_label, model_name = result.get(DJANGO_CT)[0].split('.')
            model = get_model(app_label, model_name)
            additional_fields = {}
            score = hits - weight

            if model and model in indexed_models:
                for key, value in result.items():
                    if len(value):
                        value = value[0]
                    else:
                        value = None
                    index = unified_index.get_index(model)
                    string_key = str(key)

                    if string_key in index.fields and hasattr(index.fields[string_key], 'convert'):
                        additional_fields[string_key] = index.fields[string_key].convert(value)
                    else:
                        additional_fields[string_key] = value

                del(additional_fields[DJANGO_CT])
                del(additional_fields[DJANGO_ID])

                result = result_class(app_label, model_name, result[DJANGO_ID][0], score, **additional_fields)
                results.append(result)
        return {'results': results,
                'hits': hits,
                'facets': facets}


from haystack.inputs import Clean
class CloudsearchSearchQuery(BaseSearchQuery):
    def build_query_fragment(self, field, filter_type, value):
        from haystack import connections
        query_frag = ''

        if not hasattr(value, 'input_type_name'):
            # Handle when we've got a ``ValuesListQuerySet``...
            if hasattr(value, 'values_list'):
                value = list(value)

            if isinstance(value, basestring):
                # It's not an ``InputType``. Assume ``Clean``.
                value = Clean(value)
            else:
                value = PythonData(value)

        # Prepare the query using the InputType.
        prepared_value = value.prepare(self)

        # 'content' is a special reserved word, much like 'pk' in
        # Django's ORM layer. It indicates 'no special field'.
        if field == 'content':
            index_fieldname = ''
        else:
            index_fieldname = u'%s:' % connections[self._using].get_unified_index().get_index_fieldname(field)

        filter_types = {
            'contains': u'%s',
            'startswith': u'%s*',
            'exact': u'%s',
            'gt': u'{%s TO *}',
            'gte': u'[%s TO *]',
            'lt': u'{* TO %s}',
            'lte': u'[* TO %s]',
        }

        if value.post_process is False:
            query_frag = prepared_value
        else:
            if filter_type in ['contains', 'startswith']:
                if value.input_type_name == 'exact':
                    query_frag = prepared_value
                else:
                    # Iterate over terms & incorportate the converted form of each into the query.
                    terms = []

                    if isinstance(prepared_value, basestring):
                        for possible_value in prepared_value.split(' '):
                            terms.append(filter_types[filter_type] % possible_value)
                    else:
                        terms.append(filter_types[filter_type] % prepared_value)

                    if len(terms) == 1:
                        query_frag = terms[0]
                    else:
                        query_frag = u"%s" % " AND ".join(terms)
            elif filter_type == 'in':
                in_options = []

                for possible_value in prepared_value:
                    in_options.append(u'"%s"' % possible_value)

                query_frag = u"%s" % " OR ".join(in_options)
            elif filter_type == 'range':
                start = prepared_value[0]
                end = prepared_value[1]
                query_frag = u'["%s" TO "%s"]' % (start, end)
            elif filter_type == 'exact':
                if value.input_type_name == 'exact':
                    query_frag = prepared_value
                else:
                    prepared_value = Exact(prepared_value).prepare(self)
                    query_frag = filter_types[filter_type] % prepared_value
            else:
                if value.input_type_name != 'exact':
                    prepared_value = Exact(prepared_value).prepare(self)

                query_frag = filter_types[filter_type] % prepared_value

        if len(query_frag) and not query_frag.startswith('(') and not query_frag.endswith(')'):
            query_frag = "%s" % query_frag

        s = u"%s%s" % (index_fieldname, query_frag)
        return s



class CloudsearchSearchEngine(BaseEngine):
    backend = CloudsearchSearchBackend
    query = CloudsearchSearchQuery
