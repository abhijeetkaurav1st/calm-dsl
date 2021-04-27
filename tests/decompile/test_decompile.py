import json

from calm.dsl.builtins import ref, basic_cred, CalmVariable, CalmTask, action, parallel
from calm.dsl.builtins import Service, Package, Substrate
from calm.dsl.builtins import Deployment, Profile, Blueprint
from calm.dsl.builtins import read_local_file, vm_disk_package
from calm.dsl.builtins import AhvVmDisk, AhvVmNic, AhvVmResources, AhvVm
from calm.dsl.builtins import Metadata, Ref
from calm.dsl.builtins import readiness_probe

CRED_USERNAME = read_local_file(".tests/username")
CRED_PASSWORD = read_local_file(".tests/password")
DNS_SERVER = read_local_file(".tests/dns_server")

DSL_CONFIG = json.loads(read_local_file(".tests/config.json"))
CENTOS_HADOOP_MASTER = DSL_CONFIG["AHV"]["IMAGES"]["DISK"]["CENTOS_HADOOP_MASTER"]
CENTOS_7_CLOUD_INIT = DSL_CONFIG["AHV"]["IMAGES"]["DISK"]["CENTOS_7_CLOUD_INIT"]
SQL_SERVER_2014_x64 = DSL_CONFIG["AHV"]["IMAGES"]["CD_ROM"]["SQL_SERVER_2014_x64"]

# projects
PROJECT = DSL_CONFIG["PROJECTS"]["PROJECT1"]
PROJECT_NAME = PROJECT["NAME"]

NETWORK1 = DSL_CONFIG["AHV"]["NETWORK"]["VLAN1211"]

GLOBAL_BP_CRED = basic_cred(
    CRED_USERNAME, CRED_PASSWORD, name="cred with space", default=True
)

Era_Disk = vm_disk_package(
    name="era_disk",
    config={
        # By default image type is set to DISK_IMAGE
        "image": {
            "source": "http://download.nutanix.com/era/1.1.1/ERA-Server-build-1.1.1-340d9db1118eac81219bec98507d4982045d8799.qcow2"
        }
    },
)


class MySQLService(Service):
    """Sample mysql service"""

    name = "my sql service"
    ENV = CalmVariable.Simple("DEV")

    @action
    def __create__():
        """System action for creating an application"""

        CalmTask.Exec.ssh(name="Task1", script="echo 'Service create in ENV=@@{ENV}@@'")
        MySQLService.__restart__(name="restart")

    @action
    def __restart__():
        """System action for restarting an application"""

        CalmTask.Exec.ssh(name="Task1", script="echo 'Service create in ENV=@@{ENV}@@'")


class MySQLPackage(Package):
    """Example package with variables, install tasks and link to service"""

    name = "my sql package"
    foo = CalmVariable.Simple("bar")
    services = [ref(MySQLService)]

    @action
    def __install__():
        CalmTask.Exec.ssh(name="Task1", script="echo @@{foo}@@")


class MyAhvVm1Resources(AhvVmResources):

    memory = 4
    vCPUs = 2
    cores_per_vCPU = 1
    disks = [
        AhvVmDisk.Disk.Scsi.cloneFromImageService(CENTOS_7_CLOUD_INIT, bootable=True),
        AhvVmDisk.CdRom.Sata.cloneFromImageService(SQL_SERVER_2014_x64),
        AhvVmDisk.CdRom.Ide.cloneFromImageService(SQL_SERVER_2014_x64),
        AhvVmDisk.Disk.Scsi.cloneFromImageService(CENTOS_HADOOP_MASTER),
        AhvVmDisk.Disk.Pci.allocateOnStorageContainer(12),
        AhvVmDisk.CdRom.Sata.emptyCdRom(),
        AhvVmDisk.CdRom.Ide.emptyCdRom(),
        AhvVmDisk.Disk.Scsi.cloneFromVMDiskPackage(Era_Disk),
    ]
    nics = [
        AhvVmNic.DirectNic.ingress(NETWORK1),
        AhvVmNic.NormalNic.ingress("@@{nic_var.uuid}@@"),
    ]


class MyAhvVm1(AhvVm):

    name = "@@{calm_application_name}@@-@@{calm_array_index}@@"
    resources = MyAhvVm1Resources
    categories = {"AppFamily": "Backup", "AppType": "Default"}


class AHVVMforMySQL(Substrate):
    """AHV VM config given by reading a spec file"""

    name = "ahv vm for sql"
    provider_spec = MyAhvVm1

    readiness_probe = readiness_probe(
        connection_type="SSH",
        disabled=False,
        retries="5",
        connection_port=22,
        address="@@{platform.status.resources.nic_list[0].ip_endpoint_list[0].ip}@@",
        delay_secs="0",
    )

    @action
    def __pre_create__():

        CalmTask.SetVariable.escript(
            name="Pre_create task1",
            script='nic_var={"uuid": "eab99eb7-302f-4e1a-a1a4-5cc901fb9259"}',
            target=ref(AHVVMforMySQL),
            variables=["nic_var"],
        )


class MySQLDeployment(Deployment):
    """Sample deployment pulling in service and substrate references"""

    name = "my sql deployment"
    packages = [ref(MySQLPackage)]
    substrate = ref(AHVVMforMySQL)


class PHPService(Service):
    """Sample PHP service with a custom action"""

    name = "php service"
    # Dependency to indicate PHP service is dependent on SQL service being up
    dependencies = [ref(MySQLService)]

    @action
    def test_action(name="php service test_action"):

        blah = CalmVariable.Simple("2")  # noqa
        CalmTask.Exec.ssh(name="Task2", script='echo "Hello"')
        CalmTask.Exec.ssh(name="Task3", script='echo "Hello again"')
        CalmTask.Exec.ssh(name="Task name with space", script='echo "Hello once more"')


class PHPPackage(Package):
    """Example PHP package with custom install task"""

    name = "php package"

    foo = CalmVariable.Simple("baz")
    services = [ref(PHPService)]

    @action
    def __install__():
        CalmTask.Exec.ssh(name="Task4", script="echo @@{foo}@@")


class MyAhvVm2Resources(MyAhvVm1Resources):

    memory = 2
    vCPUs = 2
    cores_per_vCPU = 2


class MyAhvVm2(AhvVm):

    name = "@@{calm_application_name}@@-@@{calm_array_index}@@"
    resources = MyAhvVm2Resources
    categories = {"AppFamily": "Backup", "AppType": "Default"}


class AHVVMforPHP(Substrate):
    """AHV VM config given by reading a spec file"""

    name = "ahv vm for php substrate"
    provider_spec = MyAhvVm2

    readiness_probe = readiness_probe(
        connection_type="SSH",
        disabled=False,
        retries="5",
        connection_port=22,
        address="@@{platform.status.resources.nic_list[0].ip_endpoint_list[0].ip}@@",
        delay_secs="0",
    )


class PHPDeployment(Deployment):
    """Sample deployment pulling in service and substrate references"""

    name = "php deplyment"

    packages = [ref(PHPPackage)]
    substrate = ref(AHVVMforPHP)


class DefaultProfile(Profile):
    """Sample application profile with variables"""

    name = "default profile"

    nameserver = CalmVariable.Simple(DNS_SERVER, label="Local DNS resolver")
    foo1 = CalmVariable.Simple("bar1", runtime=True)
    foo2 = CalmVariable.Simple("bar2", runtime=True)

    deployments = [MySQLDeployment, PHPDeployment]

    @action
    def test_profile_action(name="test profile action"):
        """Sample description for a profile action"""
        CalmTask.Exec.ssh(
            name="Task5",
            script='echo "Hello"',
            target=ref(MySQLService),
            cred=ref(GLOBAL_BP_CRED),
        )
        PHPService.test_action(name="Call Runbook Task")
        with parallel:
            CalmTask.Exec.escript(
                "print 'Hello World!'", name="Test Escript", target=ref(MySQLService)
            )
            CalmTask.SetVariable.escript(
                script="print 'var1=test'",
                name="Test Setvar Escript",
                variables=["var1"],
                target=ref(MySQLService),
            )


class TestDecompile(Blueprint):
    """Calm DSL .NEXT demo"""

    credentials = [
        basic_cred(CRED_USERNAME, CRED_PASSWORD),
        GLOBAL_BP_CRED,
        basic_cred(CRED_USERNAME, CRED_PASSWORD, name="while"),
    ]
    services = [MySQLService, PHPService]
    packages = [MySQLPackage, PHPPackage, Era_Disk]
    substrates = [AHVVMforMySQL, AHVVMforPHP]
    profiles = [DefaultProfile]


class BpMetadata(Metadata):

    project = Ref.Project(PROJECT_NAME)
