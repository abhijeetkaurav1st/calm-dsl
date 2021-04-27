"""
 Calm Runbooks with VM endpoints
"""
from calm.dsl.runbooks import read_local_file
from calm.dsl.runbooks import runbook, Ref
from calm.dsl.runbooks import RunbookTask as Task, basic_cred
from calm.dsl.runbooks import CalmEndpoint as Endpoint
from calm.dsl.builtins.models.helper.common import get_vmware_account_from_datacenter

AHV_POWER_ON = read_local_file(".tests/runbook_tests/vm_actions_ahv_on")
AHV_POWER_OFF = read_local_file(".tests/runbook_tests/vm_actions_ahv_off")
VMWARE_POWER_ON = read_local_file(".tests/runbook_tests/vm_actions_vmware_on")
VMWARE_POWER_OFF = read_local_file(".tests/runbook_tests/vm_actions_vmware_off")

CRED_USERNAME = read_local_file(".tests/runbook_tests/username")
CRED_PASSWORD = read_local_file(".tests/runbook_tests/password")
LinuxCred = basic_cred(CRED_USERNAME, CRED_PASSWORD, name="endpoint_cred")

VMWARE_ACCOUNT_NAME = get_vmware_account_from_datacenter()

AHVPoweredOnVM = Endpoint.Linux.vm(
    vms=[Ref.Vm(uuid=AHV_POWER_ON)],
    cred=LinuxCred,
    account=Ref.Account("NTNX_LOCAL_AZ"),
)

AHVPoweredOffVM = Endpoint.Linux.vm(
    vms=[Ref.Vm(uuid=AHV_POWER_OFF)],
    cred=LinuxCred,
    account=Ref.Account("NTNX_LOCAL_AZ"),
)

VMwarePoweredOnVM = Endpoint.Linux.vm(
    vms=[Ref.Vm(uuid=VMWARE_POWER_ON)],
    cred=LinuxCred,
    account=Ref.Account(VMWARE_ACCOUNT_NAME),
)

VMwarePoweredOffVM = Endpoint.Linux.vm(
    vms=[Ref.Vm(uuid=VMWARE_POWER_OFF)],
    cred=LinuxCred,
    account=Ref.Account(VMWARE_ACCOUNT_NAME),
)


@runbook
def AHVPowerOnAction(endpoints=[AHVPoweredOffVM]):
    Task.VMPowerOn(name="PowerOnTask", target=endpoints[0])


@runbook
def AHVPowerOffAction(endpoints=[AHVPoweredOnVM]):
    Task.VMRestart(name="RestartTask", target=endpoints[0])
    Task.VMPowerOff(name="PowerOffTask", target=endpoints[0])
    Task.Exec.ssh(
        name="ShellTask",
        script='''echo "Shell Task is Successful"''',
        target=endpoints[0],
    )


@runbook
def VMwarePowerOnAction(endpoints=[VMwarePoweredOffVM]):
    Task.VMPowerOn(name="PowerOnTask", target=endpoints[0])


@runbook
def VMwarePowerOffAction(endpoints=[VMwarePoweredOnVM]):
    Task.VMRestart(name="RestartTask", target=endpoints[0])
    Task.VMPowerOff(name="PowerOffTask", target=endpoints[0])
    Task.Exec.ssh(
        name="ShellTask",
        script='''echo "Shell Task is Successful"''',
        target=endpoints[0],
    )
