"""Business-operation boundary. No SQL, cryptographic primitive or key RPC exists."""
from functools import wraps
import inspect
from app.remote.state import client


def endpoint(name):
    def decorate(function):
        @wraps(function)
        def call(*args,**kwargs):
            connection = client()
            if connection is not None:
                return connection.call(name,args,kwargs)
            return function(*args,**kwargs)
        call.__office_original__ = function
        return call
    return decorate


def remote_class(name):
    def decorate(original):
        if client() is None:
            return original
        class Proxy(original):
            __office_original__ = original

            def __init__(self,*args,**kwargs):
                result = client().call(name+'.__new__',args,kwargs)
                self.__dict__.update(result['state'])
                self.__dict__['_office_object'] = result['object']

        # UI-only windows remain desktop methods; their business calls cross RPC.
        for method_name,method in inspect.getmembers(original):
            if not callable(method) or method_name.startswith('__') or method_name.startswith('open_') or method_name.startswith('more_option_'):
                continue
            if method_name in ('_get_conn','ensure_schema_minimum','ensure_role_columns','_finish_timer','_auto_check_suspension',
                               '_assert_company_access'):
                continue
            if original.__name__=='MonthlyViewLogic' and method_name in (
                'format_seconds','is_general_timer_running','is_company_timer_running','get_running_seconds',
                'get_timer_total_seconds','get_active_timer_labels','get_active_timer_details',
                'get_general_timer_detail','timer_checkpoints','_timer_elapsed','_last_day_of_month'):
                continue
            def make_call(method_name,method):
                @wraps(method)
                def invoke(self,*args,**kwargs):
                    result = client().call(name+'.'+method_name,args,kwargs,object_id=self.__dict__['_office_object'])
                    self.__dict__.update(result['state'])
                    return result['result']
                return invoke
            setattr(Proxy,method_name,make_call(method_name,method))
        Proxy.__name__,Proxy.__qualname__,Proxy.__module__ = original.__name__,original.__qualname__,original.__module__
        return Proxy
    return decorate
