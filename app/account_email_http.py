"""Public email flows; browser grants stay in a restricted HttpOnly cookie."""
from http.cookies import SimpleCookie

def dispatch(handler, path, data):
    prefix='/api/v1/auth/email/'
    native=path=='/api/v1/native/account/email'
    if not native and not path.startswith(prefix): return False
    from .server import Error
    a=handler.server.accounts
    a.rate_limit(('email-http',handler.client_address[0]),20,900)
    if not native and not handler.valid_origin(): raise Error(403,'Invalid request origin.')
    service=a.account_email
    if not service: raise Error(503,'Account email is not configured. Contact the administrator.')
    operation='request' if native else path[len(prefix):]
    headers={'Cache-Control':'no-store'}
    if operation=='request':
        result=service.request(data.get('identifier'),data.get('purpose'))
    elif operation=='open':
        purpose=data.get('purpose');token=data.get('token')
        with a.lock: service.grant(token,purpose)
        headers['Set-Cookie']='arrify_account='+purpose+':'+token+'; Path=/api/v1/auth/email/; Max-Age=1800; Secure; HttpOnly; SameSite=Strict'
        result={'purpose':purpose}
    elif operation in ('status','finish'):
        cookies=SimpleCookie();cookies.load(handler.headers.get('Cookie',''))
        value=cookies.get('arrify_account')
        purpose,_,token=(value.value if value else '').partition(':')
        if operation=='status':
            with a.lock: service.grant(token,purpose)
            result={'purpose':purpose}
        else:
            result=service.finish(token,purpose,data.get('password'))
            headers['Set-Cookie']='arrify_account=; Path=/api/v1/auth/email/; Max-Age=0; Secure; HttpOnly; SameSite=Strict'
    else: raise Error(404,'Not found.')
    handler.json(result,extra=headers)
    return True
