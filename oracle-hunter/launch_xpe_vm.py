#!/usr/bin/env python3
# ============================================================
# XPE AGENT — LANZAMIENTO AUTOMATICO EN ORACLE ALWAYS FREE
# Crea: VCN + subred + security list (22/80/443) + VM ARM 4/24GB
#       con cloud-init que instala Docker y prepara todo.
# Luego (fase 2): despliega XPE Agent (repo + .env + replicas).
# Uso:  python3 launch_xpe_vm.py  (requiere USER OCID en config)
# ============================================================
import base64, os, sys, time, subprocess, secrets, string

import oci
from oci.core import VirtualNetworkClient, ComputeClient
from oci.identity import IdentityClient

CFG = oci.config.from_file('/app/.agents/oracle/config')
if 'PENDIENTE' in CFG.get('user', ''):
    print("FALTA USER OCID en /app/.agents/oracle/config"); sys.exit(1)

TENANCY = CFG['tenancy']
REGION = CFG.get('region', 'us-ashburn-1')
comp = os.environ.get('ORACLE_COMPARTMENT_OCID', TENANCY)  # root por defecto
AD = os.environ.get('ORACLE_AD', 'kWUr:US-ASHBURN-AD-1')

network = VirtualNetworkClient(CFG)
compute = ComputeClient(CFG)
identity = IdentityClient(CFG)

# ---------- 0) availability domain real ----------
try:
    ads = identity.list_availability_domains(compartment_id=comp).data
    AD = ads[0].name
    print(f"AD: {AD}")
except Exception as e:
    print(f"AD por defecto {AD} ({e})")

# ---------- 1) imagen Oracle Linux 9 ARM mas reciente ----------
imgs = compute.list_images(compartment_id=comp, operating_system='Oracle Linux',
                           operating_system_version='9',
                           shape='VM.Standard.A1.Flex', sort_by='TIMECREATED',
                           sort_order='DESC').data
IMG = imgs[0].id if imgs else sys.exit("no image")
print(f"Imagen OL9 ARM: {IMG[:40]}...")

# ---------- 2) VCN + subnet + security list ----------
vcn = None
for v in network.list_vcns(compartment_id=comp).data:
    if v.display_name == 'xpe-vcn':
        vcn = v; break
if not vcn:
    vcn = network.create_vcn(oci.core.models.CreateVcnDetails(
        cidr_blocks=['10.0.0.0/16'], display_name='xpe-vcn',
        compartment_id=comp, dns_label='xpe')).data
    print(f"VCN creada: {vcn.id}")
wait = oci.wait_until(network, network.get_vcn(vcn.id), 'lifecycle_state', 'AVAILABLE')

sl = oci.core.models.CreateSecurityListDetails(
    compartment_id=comp, vcn_id=vcn.id, display_name='xpe-sl',
    ingress_security_rules=[
        oci.core.models.IngressSecurityRule(protocol='6', source='0.0.0.0/0',
            tcp_options=oci.core.models.TcpOptions(destination_port_range=oci.core.models.PortRange(min=22, max=22)), source_type='CIDR_BLOCK', is_stateless=False),
        oci.core.models.IngressSecurityRule(protocol='6', source='0.0.0.0/0',
            tcp_options=oci.core.models.TcpOptions(destination_port_range=oci.core.models.PortRange(min=80, max=80)), source_type='CIDR_BLOCK', is_stateless=False),
        oci.core.models.IngressSecurityRule(protocol='6', source='0.0.0.0/0',
            tcp_options=oci.core.models.TcpOptions(destination_port_range=oci.core.models.PortRange(min=443, max=443)), source_type='CIDR_BLOCK', is_stateless=False),
    ], egress_security_rules=[
        oci.core.models.EgressSecurityRule(protocol='all', destination='0.0.0.0/0',
            destination_type='CIDR_BLOCK', is_stateless=False)])

subnet = None
for s_ in network.list_subnets(compartment_id=comp).data:
    if s_.display_name == 'xpe-subnet':
        subnet = s_; break
if not subnet:
    sld = [sl for sl in network.list_security_lists(compartment_id=comp, vcn_id=vcn.id).data if sl.display_name == 'xpe-sl']
    if not sld:
        sl = network.create_security_list(sl).data
    else:
        sl = sld[0]
    subnet = network.create_subnet(oci.core.models.CreateSubnetDetails(
        compartment_id=comp, vcn_id=vcn.id, cidr_block='10.0.0.0/24',
        display_name='xpe-subnet', security_list_ids=[sl.id],
        dns_label='xpesub')).data
    oci.wait_until(network, network.get_subnet(subnet.id), 'lifecycle_state', 'AVAILABLE')
    print(f"Subred creada: {subnet.id}")

# ---------- 3) llave SSH para el agente ----------
KEYPATH = '/app/.agents/oracle/xpe_vm_ssh'
if not os.path.exists(KEYPATH):
    subprocess.run(['ssh-keygen', '-t', 'ed25519', '-N', '', '-f', KEYPATH, '-C', 'xpe-agent-vm'], check=True, capture_output=True)
pub = open(KEYPATH + '.pub').read().strip()

# ---------- 4) cloud-init: docker + firewall + swap + repo ----------
CLOUD_INIT = '''#cloud-config
package_update: true
packages: [docker-engine]
runcmd:
  - systemctl enable --now docker
  - dnf -y config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo || true
  - dnf -y install docker-ce docker-ce-cli containerd.io docker-compose-plugin
  - systemctl enable --now docker
  - firewall-offline-cmd --add-port=80/tcp --add-port=443/tcp || firewall-cmd --permanent --add-port=80/tcp --add-port=443/tcp || true
  - firewall-cmd --reload || true
  - fallocate -l 2G /xpe-swapfile && chmod 600 /xpe-swapfile && mkswap /xpe-swapfile && swapon /xpe-swapfile
  - sh -c "grep -q xpe-swapfile /etc/fstab || echo '/xpe-swapfile none swap sw 0 0' >> /etc/fstab"
  - mkdir -p /opt/xpe && cd /opt/xpe
  - git clone https://github.com/xpe-agent-respaldo/XPE-AGENT.git repo
  - touch /opt/xpe/READY
'''

# ---------- 5) lanzar la instancia ----------
inst = None
for i in compute.list_instances(compartment_id=comp).data:
    if i.display_name == 'xpe-vm-1' and i.lifecycle_state != 'TERMINATED':
        inst = i; print(f"VM ya existe: {inst.id} ({inst.lifecycle_state})"); break
if not inst:
    inst = compute.launch_instance(oci.core.models.LaunchInstanceDetails(
        compartment_id=comp, availability_domain=AD, display_name='xpe-vm-1',
        shape='VM.Standard.A1.Flex',
        shape_config=oci.core.models.LaunchInstanceShapeConfigDetails(ocpus=4, memory_in_gbs=24),
        source_details=oci.core.models.InstanceSourceViaImageDetails(image_id=IMG),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet.id, assign_public_ip=True, display_name='xpe-vnic'),
        metadata={'ssh_authorized_keys': pub,
                  'user_data': base64.b64encode(CLOUD_INIT.encode()).decode()},
        freeform_tags={'project': 'xpe-agent'},
        agent_config=oci.core.models.LaunchInstanceAgentConfigDetails(is_monitoring_disabled=True, is_management_disabled=False)
    )).data
    print(f"VM LANZADA: {inst.id}")

oci.wait_until(compute, compute.get_instance(inst.id), 'lifecycle_state', 'RUNNING', max_wait_seconds=600)
print("VM RUNNING ✅")

# ---------- 6) IP publica ----------
vnic = None
for _ in range(30):
    try:
        atts = compute.list_vnic_attachments(compartment_id=comp, instance_id=inst.id).data
        if atts:
            v = oci.core.models
            vid = atts[0].vnic_id
            vn = network.get_vnic(vid).data
            if vn.public_ip:
                vnic = vn; break
    except Exception:
        pass
    time.sleep(10)
print(f"\n{'='*60}\nIP PUBLICA DE LA VM: {vnic.public_ip if vnic else 'NO OBTENIDA'}\n"
      f"SSH: ssh -i {KEYPATH} opc@{vnic.public_ip if vnic else '<IP>'}\n{'='*60}")
open('/app/.agents/oracle/VM_IP', 'w').write(vnic.public_ip if vnic else '')
print("Guardado en /app/.agents/oracle/VM_IP")
