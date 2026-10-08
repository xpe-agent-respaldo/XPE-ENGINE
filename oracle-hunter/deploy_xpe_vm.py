#!/usr/bin/env python3
# ============================================================
# XPE AGENT — DESPLIEGUE EN LA VM ORACLE (fase 2)
# 1) Genera .env.oracle desde las env-vars de Render (via API)
# 2) Intenta SSH directo; si el puerto 22 esta bloqueado, usa
#    OCI Run Command (agente de gestion) por HTTPS.
# 3) Levanta las replicas: docker compose up -d --scale xpe=5
# 4) Verifica http://<IP>/api/health
# ============================================================
import os, sys, json, time, base64, subprocess, urllib.request, urllib.parse

REPO_PATH = '/opt/xpe/repo'
VM_IP = open('/app/.agents/oracle/VM_IP').read().strip()
SSH_KEY = '/app/.agents/oracle/xpe_vm_ssh'
SCALE = os.environ.get('XPE_SCALE', '5')

# ---------- 1) .env.oracle desde Render ----------
def render_env():
    key = os.environ.get('RENDER_API_KEY')
    req = urllib.request.Request('https://api.render.com/v1/services/srv-dav4pq3ncjis7397lek0/env-vars?limit=100',
                                 headers={'Authorization': f'Bearer {key}', 'Accept': 'application/json'})
    with urllib.request.urlopen(req) as r:
        vars_ = json.load(r)
    out = [f"{v['key']}={v.get('value','')}" for v in vars_ if v.get('key')]
    return '\n'.join(out) + '\nPORT=8080\nNODE_ENV=production\n'

# fallback: env del sandbox si la API cambia
env_content = render_env()
open('/tmp/env.oracle', 'w').write(env_content)
env_b64 = base64.b64encode(env_content.encode()).decode()

# ---------- 2) subir y ejecutar ----------
DEPLOY = f'''set -e
cd {REPO_PATH} || (mkdir -p /opt/xpe && cd /opt/xpe && git clone https://github.com/xpe-agent-respaldo/XPE-AGENT.git repo && cd repo)
echo {env_b64} | base64 -d > /opt/xpe/.env.oracle
cp oracle/docker-compose.yml oracle/Caddyfile /opt/xpe/ 2>/dev/null || true
cd /opt/xpe && git -C repo pull --ff-only || true
docker compose --env-file .env.oracle -f repo/oracle/docker-compose.yml up -d --build --scale xpe={SCALE}
sleep 3
curl -s http://127.0.0.1/api/health || true
'''

def try_ssh():
    if not os.path.exists(SSH_KEY): return None
    try:
        subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=8',
                        '-i', SSH_KEY, f'opc@{VM_IP}', 'echo ok'], capture_output=True, timeout=15)
        r = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-o', 'ConnectTimeout=8',
                            '-i', SSH_KEY, f'opc@{VM_IP}', 'echo ok'], capture_output=True, timeout=15)
        if r.returncode == 0 and b'ok' in r.stdout:
            print("SSH OK ✅ (subiendo deploy por SSH)")
            p = subprocess.run(['ssh', '-o', 'StrictHostKeyChecking=no', '-i', SSH_KEY, f'opc@{VM_IP}',
                                f"sudo bash -c '{DEPLOY}'"], capture_output=True, timeout=900)
            print(p.stdout.decode()[-2000:]); print(p.stderr.decode()[-500:])
            return p.returncode == 0
    except Exception as e:
        print(f"SSH fallo: {e}")
    return None

res = try_ssh()
if res is None:
    print("Puerto 22 bloqueado desde aqui → usando OCI Run Command")
    import oci
    from oci.compute_instance_agent import ComputeInstanceAgentClient
    CFG = oci.config.from_file('/app/.agents/oracle/config')
    compute = oci.core.ComputeClient(CFG)
    inst = [i for i in compute.list_instances(compartment_id=CFG['tenancy']).data
            if i.display_name == 'xpe-vm-1' and i.lifecycle_state == 'RUNNING'][0]
    cl = ComputeInstanceAgentClient(CFG, service_endpoint=f"https://instance-agent.{CFG['region']}.oraclecloud.com")
    cmd = cl.create_command(oci.compute_instance_agent.models.CreateCommandDetails(
        compartment_id=inst.compartment_id,
        execution_time_out_in_seconds=900,
        display_name='xpe-deploy',
        target=oci.compute_instance_agent.models.CommandTarget(instance_id=inst.id),
        content=oci.compute_instance_agent.models.InstanceAgentCommandContent(
            source=oci.compute_instance_agent.models.InstanceAgentCommandSourceViaTextDetails(
                source_type='TEXT', text=DEPLOY, output_return='ALL')))).data
    print(f"Run Command enviado: {cmd.id}")
    for _ in range(90):
        time.sleep(10)
        for d in cl.list_command_executions(compartment_id=inst.compartment_id,
                                             instance_id=inst.id).data:
            if d.command_id == cmd.id:
                if d.lifecycle_state in ('SUCCEEDED', 'FAILED', 'TIMEDOUT'):
                    print("Run Command:", d.lifecycle_state)
                    try:
                        out = cl.get_command_execution_output_content(d.id,
                            output_type='TEXT', opc_instance_agent_command_output_type='TEXT')
                        print(str(out.data)[:3000])
                    except Exception: pass
                    sys.exit(0 if d.lifecycle_state == 'SUCCEEDED' else 1)
                break
elif res:
    print("DEPLOY POR SSH TERMINADO ✅")
