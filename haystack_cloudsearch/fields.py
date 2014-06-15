# from haystack.fields import CharField, FacetField, IntegerField, FloatField, MultiValueField
from haystack import fields


class CloudSearchField(object):

    def __init__(self, **kwargs):
        self.return_enabled = kwargs.get('return_enabled', True)
        self.search_enabled = kwargs.get('search_enabled', False)
        self.sort_enabled   = kwargs.get('sort_enabled', False)
        self.source_field   = kwargs.get('source_field')
        super(CloudSearchField, self).__init__(**kwargs)

    def _get_common_options_value(self):
        common_options = {
            'DefaultValue': self._default,
            'ReturnEnabled': self.return_enabled,
            'SortEnabled': self.sort_enabled,
            'SourceField': self.source_field
        }
        return common_options

    def get_options_name(self):
        return u'%sOptions' % self.field_type.capitalize()

    def get_options_value(self):
        options = self._get_common_options_value()
        options.update({
            'FacetEnabled': self.faceted,
            'SearchEnabled': self.search_enabled,
        })
        return options

    def get_index_schema(self):
        data = {
            u'IndexFieldName': unicode(self.index_fieldname),
            u'IndexFieldType': unicode(self.field_type),
            u'DateArrayOptions': None,
            u'DateOptions': None,
            u'DoubleArrayOptions': None,
            u'DoubleOptions': None,
            u'IntArrayOptions': None,
            u'IntOptions': None,
            u'LatLonOptions': None,
            u'LiteralArrayOptions': None,
            u'LiteralOptions': None,
            u'TextArrayOptions': None,
            u'TextOptions': None
        }
        data[self.get_options_name()] = self.get_options_value()
        return data

class CharField(CloudSearchField, fields.CharField):
    field_type = 'text'

    def __init__(self, **kwargs):
        self.analysis_scheme = kwargs.get('analysis_scheme', u'_en_default_')
        self.highlight_enabled = kwargs.get('highlight_enabled', False)
        super(CharField, self).__init__(**kwargs)
        self._default = None

    def get_options_value(self):
        options = super(CharField, self)._get_common_options_value()
        options.update({
            'AnalysisScheme': self.analysis_scheme,
            'HighlightEnabled': self.highlight_enabled
        })
        return options

class DateField(CloudSearchField, fields.DateField):
    pass

class FloatField(CloudSearchField, fields.FloatField):
    field_type = 'double'

class IntegerField(CloudSearchField, fields.IntegerField):
    field_type = 'int'

    def __init__(self, **kwargs):
        super(IntegerField, self).__init__(**kwargs)
        self._default = 0

class LiteralField(CharField):
    field_type = 'literal'

    def __init__(self, **kwargs):
        if kwargs.get('facet_class') is None:
            kwargs['facet_class'] = FacetLiteralField
        super(LiteralField, self).__init__(**kwargs)

    def get_options_value(self):
        options = self._get_common_options_value()
        options.update({
            'FacetEnabled': self.faceted,
            'SearchEnabled': self.search_enabled,
        })
        return options

class FacetLiteralField(fields.FacetField, LiteralField):
    pass


class MultiValueDateField(fields.MultiValueField):
    field_type = 'date-array'

    def __init__(self, **kwargs):
        if kwargs.get('facet_class') is None:
            kwargs['facet_class'] = FacetMultiValueDateField
        super(MultiValueDateField, self).__init__(**kwargs)

class FacetMultiValueDateField(fields.FacetField, MultiValueDateField):
    pass


class MultiValueFloatField(fields.MultiValueField):
    field_type = 'double-array'

    def __init__(self, **kwargs):
        if kwargs.get('facet_class') is None:
            kwargs['facet_class'] = FacetMultiValueFloatField
        super(MultiValueFloatField, self).__init__(**kwargs)

class FacetMultiValueFloatField(fields.FacetField, MultiValueFloatField):
    pass


class MultiValueIntegerField(fields.MultiValueField):
    field_type = 'int-array'

    def __init__(self, **kwargs):
        if kwargs.get('facet_class') is None:
            kwargs['facet_class'] = FacetMultiValueIntegerField
        super(MultiValueIntegerField, self).__init__(**kwargs)

class FacetMultiValueIntegerField(fields.FacetField, MultiValueIntegerField):
    pass


class MultiValueLiteralField(fields.MultiValueField):
    field_type = 'literal-array'

    def __init__(self, **kwargs):
        if kwargs.get('facet_class') is None:
            kwargs['facet_class'] = FacetMultiValueLiteralField
        super(fields.MultiValueField, self).__init__(**kwargs)

class FacetMultiValueLiteralField(fields.FacetField, MultiValueLiteralField):
    pass


class MultiValueCharField(fields.MultiValueField):
    field_type = 'text-array'

    def __init__(self, **kwargs):
        if kwargs.get('facet_class') is None:
            kwargs['facet_class'] = FacetMultiValueCharField
        super(fields.MultiValueField, self).__init__(**kwargs)

class FacetMultiValueCharField(fields.FacetField, MultiValueCharField):
    pass
