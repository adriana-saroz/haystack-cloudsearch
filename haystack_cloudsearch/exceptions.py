class CloudsearchException(Exception):
    pass

class CloudsearchProcessingException(CloudsearchException):
    pass

class CloudsearchNeedsIndexingException(CloudsearchException):
    pass

class CloudsearchDryerExploded(Exception):
    """ This is raised when the max timeout for a spinlock is encountered. """
    pass