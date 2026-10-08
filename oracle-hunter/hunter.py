#!/usr/bin/env python3
# ============================================================
# XPE HUNTER — Caza automatica 24/7 de la VM Oracle A1 gratis
# Corre en GitHub Actions cada 10 min (cron) o en cualquier host.
# 1) Si la VM ya existe -> no hace nada
# 2) Si no existe -> intenta lanzarla (red ya creada o la crea)
# 3) Al conseguirla -> manda correo por Brevo con IP y datos
# Credenciales SIEMPRE por variables de entorno, jamas en el repo.
# ============================================================
import base64, datetime, hashlib, json, os, subprocess, tempfile, time
import urllib.request, urllib.error

TENANCY = os.environ['OCI_TENANCY']
USER = os.environ['OCI_USER']
FPR = os.environ['OCI_FINGERPRINT']
REGION = os.environ.get('OCI_REGION', 'us-ashburn-1')
KEY = os.environ['OCI_KEY'].replace('\\n', '\n')
SSH_PUBKEY = os.environ.get('SSH_PUBKEY', '')
BREVO = os.environ.get('BREVO_API_KEY', '')
ALERT_EMAIL = os.environ.get('ALERT_EMAIL', '')
SENDER_EMAIL = os.environ.get('SENDER_EMAIL', ALERT_EMAIL)

_keyfile = tempfile.NamedTemporaryFile('w', suffix='.pem', delete=False)
_keyfile.write(KEY); _keyfile.close()

def oci(method, path, host, body=None):
    url = f"https://{host}{path}"
    now = datetime.datetime.utcnow().strftime('%a, %d %b %Y %H:%M:%S GMT')
    data = json.dumps(body).encode() if body is not None else b''
    headers = {'Date': now}
    if body is not None:
        sha = base64.b64encode(hashlib.sha256(data).digest()).decode()
        headers.update({'x-content-sha256': sha, 'Content-Type': 'application/json',
                        'Content-Length': str(len(data))})
        sig = "\n".join([f"(request-target): {method} {path}", f"host: {host}", f"date: {now}",
                         f"x-content-sha256: {sha}", "content-type: application/json",
                         f"content-length: {len(data)}"])
        heads = "(request-target) host date x-content-sha256 content-type content-length"
    else:
        sig = "\n".join([f"(request-target): {method} {path}", f"host: {host}", f"date: {now}"])
        heads = "(request-target) host date"
    s = subprocess.run(['openssl', 'dgst', '-sha256', '-sign', _keyfile.name],
                       input=sig.encode(), capture_output=True)
    headers['Authorization'] = (f'Signature version="1",keyId="{TENANCY}/{USER}/{FPR}",'
                                f'algorithm="rsa-sha256",headers="{heads}",'
                                f'signature="{base64.b64encode(s.stdout).decode()}"')
    req = urllib.request.Request(url, data=data if body is not None else None,
                                 method=method.upper(), headers=headers)
    try:
        return 200, json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        try: return e.code, json.loads(e.read())
        except Exception: return e.code, {}

IAAS = f'iaas.{REGION}.oraclecloud.com'

def email(subject, html):
    if not (BREVO and ALERT_EMAIL): return
    body = {'sender': {'name': 'XPE Hunter', 'email': SENDER_EMAIL},
            'to': [{'email': ALERT_EMAIL}],
            'subject': subject, 'htmlContent': html}
    req = urllib.request.Request('https://api.brevo.com/v3/smtp/email',
        data=json.dumps(body).encode(), method='POST',
        headers={'api-key': BREVO, 'Content-Type': 'application/json'})
    try:
        urllib.request.urlopen(req, timeout=30); print('correo enviado OK')
    except Exception as e: print('fallo correo:', e)

def find_subnet():
    _, vcns = oci('get', '/20160918/vcns?compartmentId=' + TENANCY, IAAS)
    for v in vcns or []:
        subs = oci('get', '/20160918/subnets?compartmentId=' + TENANCY + '&vcnId=' + v['id'], IAAS)[1] or []
        s = next((x for x in subs if x['displayName'] == 'xpe-subnet'), None)
        if s: return s
    return None

def ensure_network():
    s = find_subnet()
    if s: return s
    _, vcns = oci('get', '/20160918/vcns?compartmentId=' + TENANCY, IAAS)
    vcn = (vcns or [None])[0]
    if not vcn:
        vcn = oci('post', '/20160918/vcns', IAAS,
                  {'compartmentId': TENANCY, 'cidrBlock': '10.0.0.0/16', 'displayName': 'xpe-vcn'})[1]
    vid = vcn['id']
    igs = oci('get', '/20160918/internetGateways?compartmentId=' + TENANCY + '&vcnId=' + vid, IAAS)[1] or []
    ig = (igs or [None])[0] or oci('post', '/20160918/internetGateways', IAAS,
        {'compartmentId': TENANCY, 'vcnId': vid, 'displayName': 'xpe-ig', 'isEnabled': True})[1]
    rts = oci('get', '/20160918/routeTables?compartmentId=' + TENANCY + '&vcnId=' + vid, IAAS)[1] or []
    rt = next((x for x in rts if x['displayName'] == 'xpe-rt'), None)
    if not rt:
        rt = oci('post', '/20160918/routeTables', IAAS, {'compartmentId': TENANCY, 'vcnId': vid,
            'displayName': 'xpe-rt',
            'routeRules': [{'destination': '0.0.0.0/0', 'destinationType': 'CIDR_BLOCK',
                            'networkEntityId': ig['id']}]})[1]
    sls = oci('get', '/20160918/securityLists?compartmentId=' + TENANCY + '&vcnId=' + vid, IAAS)[1] or []
    sl = next((x for x in sls if x['displayName'] == 'xpe-sl'), None)
    if not sl:
        rules = []
        for p in (22, 80, 443):
            rules.append({'protocol': '6', 'source': '0.0.0.0/0', 'sourceType': 'CIDR_BLOCK',
                          'isStateless': False,
                          'tcpOptions': {'destinationPortRange': {'min': p, 'max': p}}})
        sl = oci('post', '/20160918/securityLists', IAAS, {'compartmentId': TENANCY, 'vcnId': vid,
            'displayName': 'xpe-sl', 'ingressSecurityRules': rules})[1]
    for i in range(3):
        st, sub = oci('post', '/20160918/subnets', IAAS,
            {'compartmentId': TENANCY, 'vcnId': vid, 'cidrBlock': '10.0.1.0/24',
             'displayName': 'xpe-subnet', 'routeTableId': rt['id'], 'securityListIds': [sl['id']]})
        if sub.get('id'): return sub
        time.sleep(5)
    return None

def main():
    # 1) ya la tenemos?
    _, inst = oci('get', '/20160918/instances?compartmentId=' + TENANCY, IAAS)
    if inst:
        print('VM ya existe:', inst[0]['displayName'], inst[0]['lifecycleState'])
        return
    sub = ensure_network()
    if not sub:
        print('sin subnet disponible'); return
    imgs = oci('get', '/20160918/images?compartmentId=' + TENANCY +
               '&operatingSystem=Canonical%20Ubuntu&shape=VM.Standard.A1.Flex'
               '&sortBy=TIMECREATED&sortOrder=DESC', IAAS)[1] or []
    img = next((i for i in imgs if '22.04' in i['displayName'] and 'Minimal' not in i['displayName']), None)
    if not img: print('sin imagen'); return
    ads = oci('get', '/20160918/availabilityDomains?compartmentId=' + TENANCY,
              f'identity.{REGION}.oraclecloud.com')[1]
    for ad in ads[:3]:
        st, r = oci('post', '/20160918/instances', IAAS, {
            'compartmentId': TENANCY, 'availabilityDomain': ad['name'],
            'displayName': 'xpe-agent-a1', 'shape': 'VM.Standard.A1.Flex',
            'shapeConfig': {'ocpus': 4, 'memoryInGBs': 24},
            'sourceDetails': {'sourceType': 'image', 'imageId': img['id']},
            'createVnicDetails': {'subnetId': sub['id'], 'assignPublicIp': True},
            'metadata': {'ssh_authorized_keys': SSH_PUBKEY}})
        if r.get('id'):
            print('CONSEGUIDA en', ad['name'])
            # esperar IP publica
            ip = ''
            for _ in range(12):
                time.sleep(10)
                vns = oci('get', '/20160918/vnics?compartmentId=' + TENANCY +
                          '&instanceId=' + r['id'], IAAS)[1] or []
                ip = next((v.get('publicIp') for v in vns if v.get('publicIp')), '')
                if ip: break
            email('🎯 ¡XPE Hunter consiguió tu VM Oracle A1!',
                  f'<div style="font-family:sans-serif;background:#0a0a0f;color:#fff;padding:24px;border-radius:12px">'
                  f'<h2>🎯 ¡Misión cumplida!</h2><p>La VM <b>xpe-agent-a1</b> (ARM 4 OCPU / 24GB, gratis) '
                  f'ya está lanzada en <b>{ad["name"]}</b>.</p>'
                  f'<p>IP pública: <b style="color:#a78bfa">{ip or "(asignándose)"}</b></p>'
                  f'<p>SSH: <code>ssh -i xpe_vm_key ubuntu@{ip}</code></p>'
                  f'<p>Siguiente paso: desplegar el motor XPE (docker compose con réplicas). '
                  f'El hunter se apaga solo ahora que la VM existe.</p>'
                  f'<p>— XPE Hunter, el agente cazador de XPE Agent ⭐</p></div>')
            return
        print(ad['name'], '->', json.dumps(r)[:100])
    print('sin stock todavia, seguimos cazando')

if __name__ == '__main__':
    main()
