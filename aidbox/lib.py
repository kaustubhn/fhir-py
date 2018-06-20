import json

import requests
import inflection

from urllib.parse import parse_qsl, urlencode

from .utils import convert_to_underscore
from .exceptions import AidboxResourceFieldDoesNotExist, \
    AidboxResourceNotFound, AidboxAuthorizationError


class Aidbox:
    schema = None

    def __init__(self, host, token=None, email=None, password=None):
        self.schema = {}
        self.host = host

        if token:
            self.token = token
        else:
            r = requests.post(
                '{0}/oauth2/authorize'.format(host),
                params={
                    'client_id': 'sansara',
                    'scope': 'openid profile email',
                    'response_type': 'id_token',
                },
                data={'email': email, 'password': password},
                allow_redirects=False
            )
            if 'location' not in r.headers:
                raise AidboxAuthorizationError()

            token_data = dict(parse_qsl(r.headers['location']))
            self.token = token_data['id_token']

    def resource(self, resource_type, **kwargs):
        kwargs['resource_type'] = resource_type
        return AidboxResource(self, **kwargs)

    def resources(self, resource_type):
        return AidboxSearchSet(self, resource_type=resource_type)

    def _fetch_resource(self, path, params=None):
        r = requests.get(
            '{0}/{1}'.format(self.host, path),
            params=params,
            headers={'Authorization': 'Bearer {0}'.format(self.token)})
        if r.status_code == 404:
            raise AidboxResourceNotFound()
        if r.status_code == 200:
            result = json.loads(r.text)
            return convert_to_underscore(result)

        raise AidboxAuthorizationError()

    def _fetch_schema(self, resource_type):
        schema = self.schema.get(resource_type, None)

        if not schema:
            attrs_data = self._fetch_resource(
                'Attribute',
                params={'entity': resource_type}
            )
            attrs = [res['resource'] for res in attrs_data['entry']]
            schema = {inflection.underscore(attr['path'][0])
                      for attr in attrs} | {'id'}

            self.schema[resource_type] = schema

        return schema

    def __str__(self):
        return self.host

    def __repr__(self):
        return self.__str__()


class AidboxSearchSet:
    aidbox = None
    resource_type = None
    params = {}

    def __init__(self, aidbox, resource_type, params=None):
        self.aidbox = aidbox
        self.resource_type = resource_type
        self.params = params if params else {}

    def get(self, id):
        res_data = self.aidbox._fetch_resource(
            '{0}/{1}'.format(self.resource_type, id))
        return self.aidbox.resource(skip_validation=True, **res_data)

    def all(self):
        res_data = self.aidbox._fetch_resource(self.resource_type, self.params)
        resource_data = [res['resource'] for res in res_data['entry']]
        return [AidboxResource(
            self.aidbox,
            skip_validation=True,
            **data
        ) for data in resource_data]

    def first(self):
        result = self.limit(1).all()
        return result[0] if result else None

    def last(self):
        # TODO: return last item from list
        # TODO: sort (-) + first
        pass

    def search(self, **kwargs):
        self.params.update(kwargs)
        return AidboxSearchSet(self.aidbox, self.resource_type, self.params)

    def limit(self, limit):
        self.params['_count'] = limit
        return AidboxSearchSet(self.aidbox, self.resource_type, self.params)

    def page(self, page):
        self.params['_page'] = page
        return AidboxSearchSet(self.aidbox, self.resource_type, self.params)

    def sort(self, keys):
        sort_keys = ','.join(keys) if isinstance(keys, list) else keys
        self.params['_sort'] = sort_keys
        return AidboxSearchSet(self.aidbox, self.resource_type, self.params)

    def count(self):
        # TODO: rewrite
        return self.aidbox._fetch_resource(
            self.resource_type,
            params={'_count': 1, '_totalMethod': 'count'}
        )['total']

    def include(self):
        # https://www.hl7.org/fhir/search.html
        # works as select_related
        # result: Bundle [patient1, patientN, clinic1, clinicN]
        # searchset.filter(name='john').get(pk=1)
        pass

    def revinclude(self):
        # https://www.hl7.org/fhir/search.html
        # works as prefetch_related
        pass

    def __str__(self):
        return '<AidboxSearchSet {0}?{1}>'.format(
            self.resource_type, urlencode(self.params))

    def __repr__(self):
        return self.__str__()

    def __iter__(self):
        return iter(self.all())


class AidboxResource:
    aidbox = None
    resource_type = None

    data = None
    meta = None

    @property
    def root_attrs(self):
        return self.aidbox.schema[self.resource_type]

    def __init__(self, aidbox, skip_validation=False, **kwargs):
        self.data = {}
        self.aidbox = aidbox
        self.resource_type = kwargs.get('resource_type')
        self.aidbox._fetch_schema(self.resource_type)

        meta = kwargs.pop('meta', {})
        self.meta = meta

        for key, value in kwargs.items():
            try:
                setattr(self, key, value)
            except AidboxResourceFieldDoesNotExist:
                if not skip_validation:
                    raise

    def __setattr__(self, key, value):
        if key in dir(self):
            super(AidboxResource, self).__setattr__(key, value)
        elif key in self.root_attrs:
            if isinstance(value, AidboxResource):
                self.data[key] = value.reference()
            else:
                self.data[key] = value
        else:
            raise AidboxResourceFieldDoesNotExist(
                'Invalid attribute `{0}` for resource `{1}`'.format(
                    key, self.resource_type))

    def __getattr__(self, key):
        if key in self.root_attrs:
            return self.data.get(key, None)
        else:
            raise AidboxResourceFieldDoesNotExist(
                'Invalid attribute `{0}` for resource `{1}`'.format(
                    key, self.resource_type))

    def save(self):
        # pass over data and when we see type(field) == AidboxReference, then
        # convert to dict with {'resource_type': '', 'id': ''}
        # then CamelCase it and post JSON to server
        pass

    def delete(self):
        pass

    def reference(self):
        return AidboxReference(self.aidbox, self.resource_type, self.id)

    def __str__(self):
        if self.data:
            if self.id:
                return '<AidboxResource {0}/{1}>'.format(
                    self.resource_type, self.id)
            else:
                return '<AidboxResource {0}>'.format(self.resource_type)
        else:
            return '<AidboxResource>'

    def __repr__(self):
        return self.__str__()


class AidboxReference:
    aidbox = None
    resource_type = None
    id = None

    def __init__(self, aidbox, resource_type, id, **kwargs):
        self.aidbox = aidbox
        self.resource_type = resource_type
        self.id = id
        # TODO: parse kwargs (display, resource)

    def __str__(self):
        return '<AidboxReference {0}/{1}>'.format(self.resource_type, self.id)

    def __repr__(self):
        return self.__str__()
