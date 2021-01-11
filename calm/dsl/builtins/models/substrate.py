import sys

from .entity import EntityType, Entity, EntityTypeBase, EntityDict
from .validator import PropertyValidator
from .readiness_probe import readiness_probe
from .provider_spec import provider_spec
from .ahv_vm import AhvVmType
from .client_attrs import update_dsl_metadata_map, get_dsl_metadata_map
from .metadata_payload import get_metadata_obj

from calm.dsl.config import get_context
from calm.dsl.constants import CACHE, PROVIDER, VM
from calm.dsl.store import Cache
from calm.dsl.log import get_logging_handle

LOG = get_logging_handle(__name__)


# Substrate


class SubstrateDict(EntityDict):
    @staticmethod
    def pre_validate(vdict, name, value):
        if name == "readiness_probe":
            if isinstance(value, dict):
                rp_validator, is_array = vdict[name]
                rp_cls_type = rp_validator.get_kind()
                return rp_cls_type(None, (Entity,), value)

        return value


class SubstrateType(EntityType):
    __schema_name__ = "Substrate"
    __openapi_type__ = "app_substrate"
    __prepare_dict__ = SubstrateDict

    ALLOWED_FRAGMENT_ACTIONS = {
        "__pre_create__": "pre_action_create",
        "__post_delete__": "post_action_delete",
    }

    def compile(cls):

        cdict = super().compile()

        readiness_probe_dict = {}
        if "readiness_probe" in cdict and cdict["readiness_probe"]:
            readiness_probe_dict = cdict["readiness_probe"]
            if hasattr(readiness_probe_dict, "compile"):
                readiness_probe_dict = readiness_probe_dict.compile()
        else:
            readiness_probe_dict = readiness_probe().compile()

        # Fill out os specific details if not found
        if cdict["os_type"] == "Linux":
            if not readiness_probe_dict.get("connection_type", ""):
                readiness_probe_dict["connection_type"] = "SSH"

            if not readiness_probe_dict.get("connection_port", ""):
                readiness_probe_dict["connection_port"] = 22

            if not readiness_probe_dict.get("connection_protocol", ""):
                readiness_probe_dict["connection_protocol"] = ""

        else:
            if not readiness_probe_dict.get("connection_type", ""):
                readiness_probe_dict["connection_type"] = "POWERSHELL"

            if not readiness_probe_dict.get("connection_port", ""):
                readiness_probe_dict["connection_port"] = 5985

            if not readiness_probe_dict.get("connection_protocol", ""):
                readiness_probe_dict["connection_protocol"] = "http"

        # Fill out address for readiness probe if not given
        if cdict["type"] == VM.AHV:
            if not readiness_probe_dict.get("address", ""):
                readiness_probe_dict[
                    "address"
                ] = "@@{platform.status.resources.nic_list[0].ip_endpoint_list[0].ip}@@"

        elif cdict["type"] == VM.EXISTING_VM:
            if not readiness_probe_dict.get("address", ""):
                readiness_probe_dict["address"] = "@@{ip_address}@@"

        elif cdict["type"] == VM.AWS:
            if not readiness_probe_dict.get("address", ""):
                readiness_probe_dict["address"] = "@@{public_ip_address}@@"

        elif cdict["type"] == VM.K8S_POD:  # Never used (Omit after discussion)
            readiness_probe_dict["address"] = ""
            cdict.pop("editables", None)

        elif cdict["type"] == VM.AZURE:
            if not readiness_probe_dict.get("address", ""):
                readiness_probe_dict[
                    "address"
                ] = "@@{platform.publicIPAddressList[0]}@@"

        elif cdict["type"] == VM.VMWARE:
            if not readiness_probe_dict.get("address", ""):
                readiness_probe_dict["address"] = "@@{platform.ipAddressList[0]}@@"

        elif cdict["type"] == VM.GCP:
            if not readiness_probe_dict.get("address", ""):
                readiness_probe_dict[
                    "address"
                ] = "@@{platform.networkInterfaces[0].accessConfigs[0].natIP}@@"

        else:
            raise Exception("Un-supported vm type :{}".format(cdict["type"]))

        # Adding min defaults in vm spec required by each provider
        if not cdict.get("create_spec"):

            # TODO shift them to constants file
            provider_type_map = {
                VM.AWS: PROVIDER.AWS.EC2,
                VM.VMWARE: PROVIDER.VMWARE,
                VM.AHV: PROVIDER.NUTANIX.PC,  # Accounts of type nutanix are not used after 2.9
                VM.AZURE: PROVIDER.AZURE,
                VM.GCP: PROVIDER.GCP,
            }

            if cdict["type"] in provider_type_map:
                if cdict["type"] == VM.AHV:
                    # UI expects defaults. Jira: https://jira.nutanix.com/browse/CALM-20134
                    if not cdict.get("create_spec"):
                        cdict["create_spec"] = {"resources": {"nic_list": []}}

                else:
                    # Getting the account_uuid for each provider
                    # Getting the metadata obj
                    metadata_obj = get_metadata_obj()
                    project_ref = metadata_obj.get("project_reference") or dict()

                    # If project not found in metadata, it will take project from config
                    ContextObj = get_context()
                    project_config = ContextObj.get_project_config()
                    project_name = project_ref.get("name", project_config["name"])

                    project_cache_data = Cache.get_entity_data(
                        entity_type=CACHE.ENTITY.PROJECT, name=project_name
                    )
                    if not project_cache_data:
                        LOG.error(
                            "Project {} not found. Please run: calm update cache".format(
                                project_name
                            )
                        )
                        sys.exit(-1)

                    # Registered accounts
                    project_accounts = project_cache_data["accounts_data"]
                    provider_type = provider_type_map[cdict["type"]]
                    account_uuid = project_accounts.get(provider_type, "")
                    if not account_uuid:
                        LOG.error(
                            "No {} account registered in project '{}'".format(
                                provider_type, project_name
                            )
                        )
                        sys.exit(-1)

                    # Adding default spec
                    cdict["create_spec"] = {"resources": {"account_uuid": account_uuid}}

                    # Template attribute should be present for vmware spec
                    if cdict["type"] == VM.VMWARE:
                        cdict["create_spec"]["template"] = ""

        # Modifying the editable object
        provider_spec_editables = cdict.pop("editables", {})
        cdict["editables"] = {}

        if provider_spec_editables:
            cdict["editables"]["create_spec"] = provider_spec_editables

        # Popping out the editables from readiness_probe
        readiness_probe_editables = readiness_probe_dict.pop("editables_list", [])
        if readiness_probe_editables:
            cdict["editables"]["readiness_probe"] = {
                k: True for k in readiness_probe_editables
            }

        cdict["readiness_probe"] = readiness_probe_dict

        return cdict

    def pre_compile(cls):
        """Adds Ahvvm data to substrate metadata"""
        super().pre_compile()

        # Adding mapping for substrate class in case of AHV provider
        types = EntityTypeBase.get_entity_types()
        AhvVmType = types.get("AhvVm", None)

        provider_spec = cls.provider_spec
        if isinstance(provider_spec, AhvVmType):
            ui_name = getattr(cls, "name", "") or cls.__name__
            sub_metadata = get_dsl_metadata_map([cls.__schema_name__, ui_name])

            vm_dsl_name = provider_spec.__name__
            vm_display_name = getattr(provider_spec, "name", "") or vm_dsl_name

            sub_metadata[AhvVmType.__schema_name__] = {
                vm_display_name: {"dsl_name": vm_dsl_name}
            }

            update_dsl_metadata_map(
                cls.__schema_name__, entity_name=ui_name, entity_obj=sub_metadata
            )

    @classmethod
    def pre_decompile(mcls, cdict, context=[], prefix=""):

        # Handle provider_spec
        cdict = super().pre_decompile(cdict, context, prefix=prefix)
        cdict["create_spec"] = provider_spec(cdict["create_spec"])

        if "__name__" in cdict:
            cdict["__name__"] = "{}{}".format(prefix, cdict["__name__"])

        return cdict

    @classmethod
    def decompile(mcls, cdict, context=[], prefix=""):

        if cdict["type"] == "K8S_POD":
            LOG.error("Decompilation support for pod deployments is not available.")
            sys.exit(-1)

        cls = super().decompile(cdict, context=context, prefix=prefix)

        provider_spec = cls.provider_spec
        if cls.provider_type == VM.AHV:
            context = [cls.__schema_name__, getattr(cls, "name", "") or cls.__name__]
            vm_cls = AhvVmType.decompile(provider_spec, context=context, prefix=prefix)

            cls.provider_spec = vm_cls

        return cls

    def get_task_target(cls):
        return cls.get_ref()


class SubstrateValidator(PropertyValidator, openapi_type="app_substrate"):
    __default__ = None
    __kind__ = SubstrateType


def substrate(**kwargs):
    name = kwargs.get("name", None)
    bases = (Entity,)
    return SubstrateType(name, bases, kwargs)


Substrate = substrate()
