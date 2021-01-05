from .resource import ResourceAPI
from .connection import REQUEST

from calm.dsl.constants import PROVIDER


class BlueprintAPI(ResourceAPI):
    def __init__(self, connection):
        super().__init__(connection, resource_type="blueprints")
        self.UPLOAD = self.PREFIX + "/import_json"
        self.LAUNCH = self.ITEM + "/simple_launch"
        self.FULL_LAUNCH = self.ITEM + "/launch"
        self.MARKETPLACE_LAUNCH = self.PREFIX + "/marketplace_launch"
        self.LAUNCH_POLL = self.ITEM + "/pending_launches/{}"
        self.BP_EDITABLES = self.ITEM + "/runtime_editables"
        self.EXPORT_JSON = self.ITEM + "/export_json"
        self.EXPORT_JSON_WITH_SECRETS = self.ITEM + "/export_json?keep_secrets=true"
        self.EXPORT_FILE = self.ITEM + "/export_file"
        self.BROWNFIELD_VM_LIST = self.PREFIX + "/brownfield_import/vms/list"
        self.VARIABLE_VALUES = self.ITEM + "/variables/{}/values"
        self.VARIABLE_VALUES_WITH_TRLID = (
            self.VARIABLE_VALUES + "?requestId={}&trlId={}"
        )

    # TODO https://jira.nutanix.com/browse/CALM-17178
    # Blueprint creation timeout is dependent on payload.
    # So setting read timeout to 300 seconds
    def upload(self, payload):
        return self.connection._call(
            self.UPLOAD,
            verify=False,
            request_json=payload,
            method=REQUEST.METHOD.POST,
            timeout=(5, 300),
        )

    def launch(self, uuid, payload):
        return self.connection._call(
            self.LAUNCH.format(uuid),
            verify=False,
            request_json=payload,
            method=REQUEST.METHOD.POST,
        )

    def full_launch(self, uuid, payload):
        return self.connection._call(
            self.FULL_LAUNCH.format(uuid),
            verify=False,
            request_json=payload,
            method=REQUEST.METHOD.POST,
        )

    def marketplace_launch(self, payload):
        return self.connection._call(
            self.MARKETPLACE_LAUNCH,
            verify=False,
            request_json=payload,
            method=REQUEST.METHOD.POST,
        )

    def poll_launch(self, blueprint_id, request_id):
        return self.connection._call(
            self.LAUNCH_POLL.format(blueprint_id, request_id),
            verify=False,
            method=REQUEST.METHOD.GET,
        )

    def _get_editables(self, bp_uuid):
        return self.connection._call(
            self.BP_EDITABLES.format(bp_uuid), verify=False, method=REQUEST.METHOD.GET
        )

    def brownfield_vms(self, payload):
        # Adding refresh cache for call. As redis expiry is 10 mins.
        payload["filter"] += ";refresh_cache==True"
        return self.connection._call(
            self.BROWNFIELD_VM_LIST,
            verify=False,
            request_json=payload,
            method=REQUEST.METHOD.POST,
        )

    @staticmethod
    def _make_blueprint_payload(bp_name, bp_desc, bp_resources, bp_metadata=None):

        if not bp_metadata:
            bp_metadata = {"spec_version": 1, "name": bp_name, "kind": "blueprint"}

        bp_payload = {
            "spec": {
                "name": bp_name,
                "description": bp_desc or "",
                "resources": bp_resources,
            },
            "metadata": bp_metadata,
            "api_version": "3.0",
        }

        return bp_payload

    def upload_with_secrets(
        self, bp_name, bp_desc, bp_resources, bp_metadata=None, force_create=False
    ):

        # check if bp with the given name already exists
        params = {"filter": "name=={};state!=DELETED".format(bp_name)}
        res, err = self.list(params=params)
        if err:
            return None, err

        response = res.json()
        entities = response.get("entities", None)
        if entities:
            if len(entities) > 0:
                if not force_create:
                    err_msg = "Blueprint {} already exists. Use --force to first delete existing blueprint before create.".format(
                        bp_name
                    )
                    # ToDo: Add command to edit Blueprints
                    err = {"error": err_msg, "code": -1}
                    return None, err

                # --force option used in create. Delete existing blueprint with same name.
                bp_uuid = entities[0]["metadata"]["uuid"]
                _, err = self.delete(bp_uuid)
                if err:
                    return None, err

        # Remove creds before upload
        creds = bp_resources.get("credential_definition_list", []) or []
        secret_map = {}
        for cred in creds:
            name = cred["name"]
            secret_map[name] = cred.pop("secret", {})
            # Explicitly set defaults so that secret is not created at server
            # TODO - Fix bug in server: {} != None
            cred["secret"] = {
                "attrs": {"is_secret_modified": False, "secret_reference": None}
            }

        # Strip secret variable values
        # TODO: Refactor and/or clean this up later
        secret_variables = []

        def strip_entity_secret_variables(path_list, obj, field_name="variable_list"):
            for var_idx, variable in enumerate(obj.get(field_name, []) or []):
                if variable["type"] == "SECRET":
                    secret_variables.append(
                        (path_list + [field_name, var_idx], variable.pop("value"))
                    )
                    variable["attrs"] = {
                        "is_secret_modified": False,
                        "secret_reference": None,
                    }

        def strip_action_secret_varaibles(path_list, obj):
            for action_idx, action in enumerate(obj.get("action_list", []) or []):
                runbook = action.get("runbook", {}) or {}
                if not runbook:
                    return
                strip_entity_secret_variables(
                    path_list + ["action_list", action_idx, "runbook"], runbook
                )
                tasks = runbook.get("task_definition_list", []) or []
                for task_idx, task in enumerate(tasks):
                    if task.get("type", None) != "HTTP":
                        continue
                    auth = (task.get("attrs", {}) or {}).get("authentication", {}) or {}
                    if auth.get("auth_type", None) == "basic":
                        secret_variables.append(
                            (
                                path_list
                                + [
                                    "action_list",
                                    action_idx,
                                    "runbook",
                                    "task_definition_list",
                                    task_idx,
                                    "attrs",
                                    "authentication",
                                    "basic_auth",
                                    "password",
                                ],
                                auth["basic_auth"]["password"].pop("value"),
                            )
                        )
                        auth["basic_auth"]["password"] = {
                            "attrs": {
                                "is_secret_modified": False,
                                "secret_reference": None,
                            }
                        }
                        if not (task.get("attrs", {}) or {}).get("headers", []) or []:
                            continue
                        strip_entity_secret_variables(
                            path_list
                            + [
                                "action_list",
                                action_idx,
                                "runbook",
                                "task_definition_list",
                                task_idx,
                                "attrs",
                            ],
                            task["attrs"],
                            field_name="headers",
                        )

        def strip_all_secret_variables(path_list, obj):
            strip_entity_secret_variables(path_list, obj)
            strip_action_secret_varaibles(path_list, obj)

        object_lists = [
            "service_definition_list",
            "package_definition_list",
            "substrate_definition_list",
            "app_profile_list",
        ]
        for object_list in object_lists:
            for obj_idx, obj in enumerate(bp_resources.get(object_list, []) or []):
                strip_all_secret_variables([object_list, obj_idx], obj)

                # Currently, deployment actions and variables are unsupported.
                # Uncomment the following lines if and when the API does support them.
                # if object_list == "app_profile_list":
                #     for dep_idx, dep in enumerate(obj["deployment_create_list"]):
                #         strip_all_secret_variables(
                #             [object_list, obj_idx, "deployment_create_list", dep_idx],
                #             dep,
                #         )

        # Handling vmware secrets
        def strip_vmware_secrets(path_list, obj):
            path_list.extend(["create_spec", "resources", "guest_customization"])
            obj = obj["create_spec"]["resources"]["guest_customization"]

            if "windows_data" in obj:
                path_list.append("windows_data")
                obj = obj["windows_data"]

                # Check for admin_password
                if "password" in obj:
                    secret_variables.append(
                        (path_list + ["password"], obj["password"].pop("value", ""))
                    )
                    obj["password"]["attrs"] = {
                        "is_secret_modified": False,
                        "secret_reference": None,
                    }

                # Now check for domain password
                if obj.get("is_domain", False):
                    if "domain_password" in obj:
                        secret_variables.append(
                            (
                                path_list + ["domain_password"],
                                obj["domain_password"].pop("value", ""),
                            )
                        )
                        obj["domain_password"]["attrs"] = {
                            "is_secret_modified": False,
                            "secret_reference": None,
                        }

        for obj_index, obj in enumerate(
            bp_resources.get("substrate_definition_list", []) or []
        ):
            if (obj["type"] == PROVIDER.VM.VMWARE) and (obj["os_type"] == "Windows"):
                strip_vmware_secrets(["substrate_definition_list", obj_index], obj)

        upload_payload = self._make_blueprint_payload(
            bp_name, bp_desc, bp_resources, bp_metadata
        )

        # TODO strip categories and add at updating time
        bp_categories = upload_payload["metadata"].pop("categories", {})
        res, err = self.upload(upload_payload)

        if err:
            return res, err

        # Add secrets and update bp
        bp = res.json()
        del bp["status"]

        # Add secrets back
        creds = bp["spec"]["resources"]["credential_definition_list"]
        for cred in creds:
            name = cred["name"]
            cred["secret"] = secret_map[name]

        for path, secret in secret_variables:
            variable = bp["spec"]["resources"]
            for sub_path in path:
                variable = variable[sub_path]
            variable["attrs"] = {"is_secret_modified": True}
            variable["value"] = secret

        # Adding categories at PUT call to blueprint
        bp["metadata"]["categories"] = bp_categories

        # Update blueprint
        update_payload = bp
        uuid = bp["metadata"]["uuid"]

        return self.update(uuid, update_payload)

    def export_json(self, uuid):
        url = self.EXPORT_JSON.format(uuid)
        return self.connection._call(url, verify=False, method=REQUEST.METHOD.GET)

    def export_json_with_secrets(self, uuid):
        url = self.EXPORT_JSON_WITH_SECRETS.format(uuid)
        return self.connection._call(url, verify=False, method=REQUEST.METHOD.GET)

    def export_file(self, uuid):
        return self.connection._call(
            self.EXPORT_FILE.format(uuid), verify=False, method=REQUEST.METHOD.GET
        )

    def variable_values(self, uuid, var_uuid):
        url = self.VARIABLE_VALUES.format(uuid, var_uuid)
        return self.connection._call(
            url, verify=False, method=REQUEST.METHOD.GET, ignore_error=True
        )

    def variable_values_from_trlid(self, uuid, var_uuid, req_id, trl_id):
        url = self.VARIABLE_VALUES_WITH_TRLID.format(uuid, var_uuid, req_id, trl_id)
        return self.connection._call(
            url, verify=False, method=REQUEST.METHOD.GET, ignore_error=True
        )
