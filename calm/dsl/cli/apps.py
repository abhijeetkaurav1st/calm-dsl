import os
import sys
import time
import json
import uuid
from json import JSONEncoder

import arrow
import click
from prettytable import PrettyTable
from anytree import NodeMixin, RenderTree

from calm.dsl.api import get_api_client
from calm.dsl.config import get_context

from .utils import get_name_query, get_states_filter, highlight_text, Display
from .constants import APPLICATION, RUNLOG, SYSTEM_ACTIONS
from .bps import (
    launch_blueprint_simple,
    compile_blueprint,
    create_blueprint,
    parse_launch_runtime_vars,
)
from calm.dsl.log import get_logging_handle

LOG = get_logging_handle(__name__)


def get_apps(name, filter_by, limit, offset, quiet, all_items, out):
    client = get_api_client()

    params = {"length": limit, "offset": offset}
    filter_query = ""
    if name:
        filter_query = get_name_query([name])
    if filter_by:
        filter_query = filter_query + ";(" + filter_by + ")"
    if all_items:
        filter_query += get_states_filter(APPLICATION.STATES, state_key="_state")
    if filter_query.startswith(";"):
        filter_query = filter_query[1:]

    if filter_query:
        params["filter"] = filter_query

    res, err = client.application.list(params=params)

    if err:
        ContextObj = get_context()
        server_config = ContextObj.get_server_config()
        pc_ip = server_config["pc_ip"]

        LOG.warning("Cannot fetch applications from {}".format(pc_ip))
        return

    res = res.json()
    total_matches = res["metadata"]["total_matches"]
    if total_matches > limit:
        LOG.warning(
            "Displaying {} out of {} entities. Please use --limit and --offset option for more results.".format(
                limit, total_matches
            )
        )

    if out == "json":
        click.echo(json.dumps(res, indent=4, separators=(",", ": ")))
        return

    json_rows = res["entities"]
    if not json_rows:
        click.echo(highlight_text("No application found !!!\n"))
        return

    if quiet:
        for _row in json_rows:
            row = _row["status"]
            click.echo(highlight_text(row["name"]))
        return

    table = PrettyTable()
    table.field_names = [
        "NAME",
        "SOURCE BLUEPRINT",
        "STATE",
        "PROJECT",
        "OWNER",
        "CREATED ON",
        "LAST UPDATED",
        "UUID",
    ]
    for _row in json_rows:
        row = _row["status"]
        metadata = _row["metadata"]

        project = (
            metadata["project_reference"]["name"]
            if "project_reference" in metadata
            else None
        )

        creation_time = int(metadata["creation_time"]) // 1000000
        last_update_time = int(metadata["last_update_time"]) // 1000000

        table.add_row(
            [
                highlight_text(row["name"]),
                highlight_text(row["resources"]["app_blueprint_reference"]["name"]),
                highlight_text(row["state"]),
                highlight_text(project),
                highlight_text(metadata["owner_reference"]["name"]),
                highlight_text(time.ctime(creation_time)),
                "{}".format(arrow.get(last_update_time).humanize()),
                highlight_text(row["uuid"]),
            ]
        )
    click.echo(table)


def _get_app(client, app_name, screen=Display(), all=False):
    # 1. Get app_uuid from list api
    params = {"filter": "name=={}".format(app_name)}
    if all:
        params["filter"] += get_states_filter(APPLICATION.STATES, state_key="_state")

    res, err = client.application.list(params=params)
    if err:
        raise Exception("[{}] - {}".format(err["code"], err["error"]))

    response = res.json()
    entities = response.get("entities", None)
    app = None
    if entities:
        app = entities[0]
        if len(entities) != 1:
            # If more than one item found, check if an exact name match is present. Else raise.
            found = False
            for ent in entities:
                if ent["metadata"]["name"] == app_name:
                    app = ent
                    found = True
                    break
            if not found:
                raise Exception("More than one app found - {}".format(entities))

        screen.clear()
        LOG.info("App {} found".format(app_name))
        screen.refresh()
        app = entities[0]
    else:
        raise Exception("No app found with name {} found".format(app_name))
    app_id = app["metadata"]["uuid"]

    # 2. Get app details
    screen.clear()
    LOG.info("Fetching app details")
    screen.refresh()
    res, err = client.application.read(app_id)
    if err:
        raise Exception("[{}] - {}".format(err["code"], err["error"]))
    app = res.json()
    return app


def describe_app(app_name, out):
    client = get_api_client()
    app = _get_app(client, app_name, all=True)

    if out == "json":
        click.echo(json.dumps(app, indent=4, separators=(",", ": ")))
        return

    click.echo("\n----Application Summary----\n")
    app_name = app["metadata"]["name"]
    click.echo(
        "Name: "
        + highlight_text(app_name)
        + " (uuid: "
        + highlight_text(app["metadata"]["uuid"])
        + ")"
    )
    click.echo("Status: " + highlight_text(app["status"]["state"]))
    click.echo(
        "Owner: " + highlight_text(app["metadata"]["owner_reference"]["name"]), nl=False
    )
    click.echo(
        " Project: " + highlight_text(app["metadata"]["project_reference"]["name"])
    )

    click.echo(
        "Blueprint: "
        + highlight_text(app["status"]["resources"]["app_blueprint_reference"]["name"])
    )

    created_on = int(app["metadata"]["creation_time"]) // 1000000
    past = arrow.get(created_on).humanize()
    click.echo(
        "Created: {} ({})".format(
            highlight_text(time.ctime(created_on)), highlight_text(past)
        )
    )

    click.echo(
        "Application Profile: "
        + highlight_text(
            app["status"]["resources"]["app_profile_config_reference"]["name"]
        )
    )

    deployment_list = app["status"]["resources"]["deployment_list"]
    click.echo("Deployments [{}]:".format(highlight_text((len(deployment_list)))))
    for deployment in deployment_list:
        click.echo(
            "\t {} {}".format(
                highlight_text(deployment["name"]), highlight_text(deployment["state"])
            )
        )

    action_list = app["status"]["resources"]["action_list"]
    click.echo("App Actions [{}]:".format(highlight_text(len(action_list))))
    for action in action_list:
        action_name = action["name"]
        if action_name.startswith("action_"):
            prefix_len = len("action_")
            action_name = action_name[prefix_len:]
        click.echo("\t" + highlight_text(action_name))

    variable_list = app["status"]["resources"]["variable_list"]
    click.echo("App Variables [{}]".format(highlight_text(len(variable_list))))
    for variable in variable_list:
        click.echo(
            "\t{}: {}  # {}".format(
                highlight_text(variable["name"]),
                highlight_text(variable["value"]),
                highlight_text(variable["label"]),
            )
        )

    click.echo("App Runlogs:")

    def display_runlogs(screen):
        watch_app(app_name, screen, app)

    Display.wrapper(display_runlogs, watch=False)

    click.echo(
        "# Hint: You can run actions on the app using: calm run action <action_name> --app {}".format(
            app_name
        )
    )


def create_app(
    bp_file,
    brownfield_deployment_file=None,
    app_name=None,
    profile_name=None,
    patch_editables=True,
    launch_params=None,
):
    client = get_api_client()

    # Compile blueprint
    bp_payload = compile_blueprint(
        bp_file, brownfield_deployment_file=brownfield_deployment_file
    )
    if bp_payload is None:
        LOG.error("User blueprint not found in {}".format(bp_file))
        sys.exit(-1)

    # Get the blueprint type
    bp_type = bp_payload["spec"]["resources"].get("type", "")

    # Create blueprint from dsl file
    bp_name = "Blueprint{}".format(str(uuid.uuid4())[:10])
    LOG.info("Creating blueprint {}".format(bp_name))
    res, err = create_blueprint(client=client, bp_payload=bp_payload, name=bp_name)
    if err:
        LOG.error(err["error"])
        return

    bp = res.json()
    bp_state = bp["status"].get("state", "DRAFT")
    bp_uuid = bp["metadata"].get("uuid", "")

    if bp_state != "ACTIVE":
        LOG.debug("message_list: {}".format(bp["status"].get("message_list", [])))
        LOG.error("Blueprint {} went to {} state".format(bp_name, bp_state))
        sys.exit(-1)

    LOG.info(
        "Blueprint {}(uuid={}) created successfully.".format(
            highlight_text(bp_name), highlight_text(bp_uuid)
        )
    )

    # Creating an app
    app_name = app_name or "App{}".format(str(uuid.uuid4())[:10])
    LOG.info("Creating app {}".format(app_name))
    launch_blueprint_simple(
        blueprint_name=bp_name,
        app_name=app_name,
        profile_name=profile_name,
        patch_editables=patch_editables,
        launch_params=launch_params,
        is_brownfield=True if bp_type == "BROWNFIELD" else False,
    )

    if bp_type != "BROWNFIELD":
        # Delete the blueprint
        res, err = client.blueprint.delete(bp_uuid)
        if err:
            raise Exception("[{}] - {}".format(err["code"], err["error"]))


class RunlogNode(NodeMixin):
    def __init__(self, runlog, parent=None, children=None):
        self.runlog = runlog
        self.parent = parent
        if children:
            self.children = children


class RunlogJSONEncoder(JSONEncoder):
    def default(self, obj):

        if not isinstance(obj, RunlogNode):
            return super().default(obj)

        metadata = obj.runlog["metadata"]
        status = obj.runlog["status"]
        state = status["state"]

        if status["type"] == "task_runlog":
            name = status["task_reference"]["name"]
        elif status["type"] == "runbook_runlog":
            if "call_runbook_reference" in status:
                name = status["call_runbook_reference"]["name"]
            else:
                name = status["runbook_reference"]["name"]
        elif status["type"] == "action_runlog" and "action_reference" in status:
            name = status["action_reference"]["name"]
        elif status["type"] == "app":
            return status["name"]
        else:
            return "root"

        # TODO - Fix KeyError for action_runlog
        """
        elif status["type"] == "action_runlog":
            name = status["action_reference"]["name"]
        elif status["type"] == "app":
            return status["name"]
        """

        creation_time = int(metadata["creation_time"]) // 1000000
        username = (
            status["userdata_reference"]["name"]
            if "userdata_reference" in status
            else None
        )
        last_update_time = int(metadata["last_update_time"]) // 1000000

        encodedStringList = []
        encodedStringList.append("{} (Status: {})".format(name, state))
        if status["type"] == "action_runlog":
            encodedStringList.append("\tRunlog UUID: {}".format(metadata["uuid"]))
        encodedStringList.append("\tStarted: {}".format(time.ctime(creation_time)))

        if username:
            encodedStringList.append("\tRun by: {}".format(username))
        if state in RUNLOG.TERMINAL_STATES:
            encodedStringList.append(
                "\tFinished: {}".format(time.ctime(last_update_time))
            )
        else:
            encodedStringList.append(
                "\tLast Updated: {}".format(time.ctime(last_update_time))
            )

        return "\n".join(encodedStringList)


def get_completion_func(screen):
    def is_action_complete(response):

        entities = response["entities"]
        if len(entities):

            # Sort entities based on creation time
            sorted_entities = sorted(
                entities, key=lambda x: int(x["metadata"]["creation_time"])
            )

            # Create nodes of runlog tree and a map based on uuid
            root = None
            nodes = {}
            for runlog in sorted_entities:
                # Create root node
                # TODO - Get details of root node
                if not root:
                    root_uuid = runlog["status"]["root_reference"]["uuid"]
                    root_runlog = {
                        "metadata": {"uuid": root_uuid},
                        "status": {"type": "action_runlog", "state": ""},
                    }
                    root = RunlogNode(root_runlog)
                    nodes[str(root_uuid)] = root

                uuid = runlog["metadata"]["uuid"]
                nodes[str(uuid)] = RunlogNode(runlog, parent=root)

            # Attach parent to nodes
            for runlog in sorted_entities:
                uuid = runlog["metadata"]["uuid"]
                parent_uuid = runlog["status"]["parent_reference"]["uuid"]
                node = nodes[str(uuid)]
                node.parent = nodes[str(parent_uuid)]

            # Show Progress
            # TODO - Draw progress bar
            total_tasks = 0
            completed_tasks = 0
            for runlog in sorted_entities:
                runlog_type = runlog["status"]["type"]
                if runlog_type == "task_runlog":
                    total_tasks += 1
                    state = runlog["status"]["state"]
                    if state in RUNLOG.STATUS.SUCCESS:
                        completed_tasks += 1

            if total_tasks:
                screen.clear()
                progress = "{0:.2f}".format(completed_tasks / total_tasks * 100)
                screen.print_at("Progress: {}%".format(progress), 0, 0)

            # Render Tree on next line
            line = 1
            for pre, fill, node in RenderTree(root):
                lines = json.dumps(node, cls=RunlogJSONEncoder).split("\\n")
                for linestr in lines:
                    tabcount = linestr.count("\\t")
                    if not tabcount:
                        screen.print_at("{}{}".format(pre, linestr), 0, line)
                    else:
                        screen.print_at(
                            "{}{}".format(fill, linestr.replace("\\t", "")), 0, line
                        )
                    line += 1
            screen.refresh()

            for runlog in sorted_entities:
                state = runlog["status"]["state"]
                if state in RUNLOG.FAILURE_STATES:
                    msg = "Action failed."
                    screen.print_at(msg, 0, line)
                    screen.refresh()
                    return (True, msg)
                if state not in RUNLOG.TERMINAL_STATES:
                    return (False, "")

            msg = "Action ran successfully."
            if os.isatty(sys.stdout.fileno()):
                msg += " Exit screen? "
            screen.print_at(msg, 0, line)
            screen.refresh()

            return (True, msg)
        return (False, "")

    return is_action_complete


def watch_action(runlog_uuid, app_name, client, screen, poll_interval=10):
    app = _get_app(client, app_name, screen=screen)
    app_uuid = app["metadata"]["uuid"]

    url = client.application.ITEM.format(app_uuid) + "/app_runlogs/list"
    payload = {"filter": "root_reference=={}".format(runlog_uuid)}

    def poll_func():
        return client.application.poll_action_run(url, payload)

    poll_action(poll_func, get_completion_func(screen), poll_interval)


def watch_app(app_name, screen, app=None, poll_interval=10):
    """Watch an app"""

    client = get_api_client()
    is_app_describe = False

    if not app:
        app = _get_app(client, app_name, screen=screen)
    else:
        is_app_describe = True
    app_id = app["metadata"]["uuid"]
    url = client.application.ITEM.format(app_id) + "/app_runlogs/list"

    payload = {
        "filter": "application_reference=={};(type==action_runlog,type==audit_runlog,type==ngt_runlog,type==clone_action_runlog)".format(
            app_id
        )
    }

    def poll_func():
        # screen.echo("Polling app status...")
        return client.application.poll_action_run(url, payload)

    def is_complete(response):
        entities = response["entities"]

        if len(entities):

            # Sort entities based on creation time
            sorted_entities = sorted(
                entities, key=lambda x: int(x["metadata"]["creation_time"])
            )

            # Create nodes of runlog tree and a map based on uuid
            root = RunlogNode(
                {
                    "metadata": {"uuid": app_id},
                    "status": {"type": "app", "state": "", "name": app_name},
                }
            )
            nodes = {}
            nodes[app_id] = root
            for runlog in sorted_entities:
                uuid = runlog["metadata"]["uuid"]
                nodes[str(uuid)] = RunlogNode(runlog, parent=root)

            # Attach parent to nodes
            for runlog in sorted_entities:
                uuid = runlog["metadata"]["uuid"]
                parent_uuid = runlog["status"]["application_reference"]["uuid"]
                node = nodes[str(uuid)]
                node.parent = nodes[str(parent_uuid)]

            # Show Progress
            # TODO - Draw progress bar
            total_tasks = 0
            completed_tasks = 0
            for runlog in sorted_entities:
                runlog_type = runlog["status"]["type"]
                if runlog_type == "action_runlog":
                    total_tasks += 1
                    state = runlog["status"]["state"]
                    if state in RUNLOG.STATUS.SUCCESS:
                        completed_tasks += 1

            if not is_app_describe and total_tasks:
                screen.clear()
                progress = "{0:.2f}".format(completed_tasks / total_tasks * 100)
                screen.print_at("Progress: {}%".format(progress), 0, 0)

            # Render Tree on next line
            line = 1
            for pre, fill, node in RenderTree(root):
                lines = json.dumps(node, cls=RunlogJSONEncoder).split("\\n")
                for linestr in lines:
                    tabcount = linestr.count("\\t")
                    if not tabcount:
                        screen.print_at("{}{}".format(pre, linestr), 0, line)
                    else:
                        screen.print_at(
                            "{}{}".format(fill, linestr.replace("\\t", "")), 0, line
                        )
                    line += 1
            screen.refresh()

            msg = ""
            is_complete = True
            if not is_app_describe:
                for runlog in sorted_entities:
                    state = runlog["status"]["state"]
                    if state in RUNLOG.FAILURE_STATES:
                        msg = "Action failed."
                        is_complete = True
                    if state not in RUNLOG.TERMINAL_STATES:
                        is_complete = False

            if is_complete:
                if not msg:
                    msg = "Action ran successfully."

                if os.isatty(sys.stdout.fileno()):
                    msg += " Exit screen? "
            if not is_app_describe:
                screen.print_at(msg, 0, line)
                screen.refresh()
                time.sleep(10)
            return (is_complete, msg)
        return (False, "")

    poll_action(poll_func, is_complete, poll_interval=poll_interval)


def delete_app(app_names, soft=False):
    client = get_api_client()

    for app_name in app_names:
        app = _get_app(client, app_name)
        app_id = app["metadata"]["uuid"]
        action_label = "Soft Delete" if soft else "Delete"
        LOG.info("Triggering {}".format(action_label))
        res, err = client.application.delete(app_id, soft_delete=soft)
        if err:
            raise Exception("[{}] - {}".format(err["code"], err["error"]))

        LOG.info("{} action triggered".format(action_label))
        response = res.json()
        runlog_id = response["status"]["runlog_uuid"]
        LOG.info("Action runlog uuid: {}".format(runlog_id))


def get_action_var_val_from_launch_params(launch_vars, var_name):
    """Returns variable value from launch params"""

    filtered_launch_vars = list(
        filter(
            lambda e: e["name"] == var_name,
            launch_vars,
        )
    )

    if len(filtered_launch_vars) > 1:
        LOG.error(
            "Unable to populate runtime editables: Multiple matches for value of variable '{}'".format(
                var_name
            )
        )
        sys.exit(-1)

    if len(filtered_launch_vars) == 1:
        return filtered_launch_vars[0].get("value", {}).get("value", None)

    return None


def get_action_runtime_args(
    app_uuid, action_payload, patch_editables, runtime_params_file
):
    """Returns action arguments or variable data"""

    action_name = action_payload["name"]

    runtime_vars = {}
    runbook_vars = action_payload["runbook"].get("variable_list", None) or []
    for _var in runbook_vars:
        editable_dict = _var.get("editables", None) or {}
        if editable_dict.get("value", False):
            runtime_vars[_var["name"]] = _var

    client = get_api_client()
    res, err = client.application.action_variables(
        app_id=app_uuid, action_name=action_name
    )
    if err:
        raise Exception("[{}] - {}".format(err["code"], err["error"]))

    action_args = res.json()

    # If no need to patch editable or there is not runtime var, return action args received from api
    if not (patch_editables and runtime_vars):
        return action_args or []

    # If file is supplied for launch params
    if runtime_params_file:
        click.echo("Patching values for runtime variables under action ...")

        parsed_runtime_vars = parse_launch_runtime_vars(
            launch_params=runtime_params_file
        )
        for _arg in action_args:
            var_name = _arg["name"]
            if var_name in runtime_vars:

                new_val = get_action_var_val_from_launch_params(
                    launch_vars=parsed_runtime_vars, var_name=var_name
                )
                if new_val is not None:
                    _arg["value"] = new_val

        return action_args

    # Else prompt for runtime variable values
    click.echo(
        "Found runtime variables in action. Please provide values for runtime variables"
    )

    for _arg in action_args:
        if _arg["name"] in runtime_vars:

            _var = runtime_vars[_arg["name"]]
            options = _var.get("options", {})
            choices = options.get("choices", [])
            click.echo("")
            if choices:
                click.echo("Choose from given choices: ")
                for choice in choices:
                    click.echo("\t{}".format(highlight_text(repr(choice))))

            default_val = _arg["value"]
            is_secret = _var.get("type") == "SECRET"

            new_val = click.prompt(
                "Value for variable '{}' [{}]".format(
                    _arg["name"],
                    highlight_text(default_val if not is_secret else "*****"),
                ),
                default=default_val,
                show_default=False,
                hide_input=is_secret,
                type=click.Choice(choices) if choices else type(default_val),
                show_choices=False,
            )

            _arg["value"] = new_val

    return action_args


def get_snapshot_name_arg(config, config_task_id):
    default_value = next(
        (
            var["value"]
            for var in config["variable_list"]
            if var["name"] == "snapshot_name"
        ),
        "",
    )
    val = click.prompt(
        "Value for Snapshot Name [{}]".format(highlight_text(repr(default_value))),
        default=default_value,
        show_default=False,
    )
    return {"name": "snapshot_name", "value": val, "task_uuid": config_task_id}


def get_recovery_point_group_arg(config, config_task_id, recovery_groups):
    choices = {}
    for i, rg in enumerate(recovery_groups):
        choices[i + 1] = {
            "label": "{}. {} [Created On: {} Expires On: {}]".format(
                i + 1,
                rg["status"]["name"],
                time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.gmtime(
                        rg["status"]["recovery_point_info_list"][0]["creation_time"]
                        // 1000000
                    ),
                ),
                time.strftime(
                    "%Y-%m-%d %H:%M:%S",
                    time.gmtime(
                        rg["status"]["recovery_point_info_list"][0]["expiration_time"]
                        // 1000000
                    ),
                ),
            ),
            "uuid": rg["status"]["uuid"],
        }
    if not choices:
        LOG.error(
            "No recovery group found. Please take a snapshot before running restore action"
        )
        sys.exit(-1)
    default_idx = 1

    click.echo("Choose from given choices: ")
    for choice in choices.values():
        click.echo("\t{}".format(highlight_text(repr(choice["label"]))))
    selected_val = click.prompt(
        "Selected Recovery Group [{}]".format(highlight_text(repr(default_idx))),
        default=default_idx,
        show_default=False,
    )
    if selected_val not in choices:
        LOG.error(
            "Invalid value {}, not present in choices: {}".format(
                selected_val, choices.keys()
            )
        )
        sys.exit(-1)
    return {
        "name": "recovery_point_group_uuid",
        "value": choices[selected_val]["uuid"],
        "task_uuid": config_task_id,
    }


def run_actions(
    app_name, action_name, watch, patch_editables=False, runtime_params_file=None
):
    client = get_api_client()

    if action_name.lower() == SYSTEM_ACTIONS.CREATE:
        click.echo(
            "The Create Action is triggered automatically when you deploy a blueprint. It cannot be run separately."
        )
        return
    if action_name.lower() == SYSTEM_ACTIONS.DELETE:
        delete_app([app_name])  # Because Delete requries a differernt API workflow
        return
    if action_name.lower() == SYSTEM_ACTIONS.SOFT_DELETE:
        delete_app(
            [app_name], soft=True
        )  # Because Soft Delete also requries the differernt API workflow
        return

    app = _get_app(client, app_name)
    app_spec = app["spec"]
    app_id = app["metadata"]["uuid"]

    calm_action_name = "action_" + action_name.lower()
    action_payload = next(
        (
            action
            for action in app_spec["resources"]["action_list"]
            if action["name"] == calm_action_name or action["name"] == action_name
        ),
        None,
    )
    if not action_payload:
        LOG.error("No action found matching name {}".format(action_name))
        sys.exit(-1)

    action_id = action_payload["uuid"]

    action_args = get_action_runtime_args(
        app_uuid=app_id,
        action_payload=action_payload,
        patch_editables=patch_editables,
        runtime_params_file=runtime_params_file,
    )

    # Hit action run api (with metadata and minimal spec: [args, target_kind, target_uuid])
    status = app.pop("status")
    config_list = status["resources"]["snapshot_config_list"]
    config_list.extend(status["resources"]["restore_config_list"])
    for task in action_payload["runbook"]["task_definition_list"]:
        if task["type"] == "CALL_CONFIG":
            config = next(
                config
                for config in config_list
                if config["uuid"] == task["attrs"]["config_spec_reference"]["uuid"]
            )
            if config["type"] == "AHV_SNAPSHOT":
                action_args.append(get_snapshot_name_arg(config, task["uuid"]))
            elif config["type"] == "AHV_RESTORE":
                substrate_id = next(
                    (
                        dep["substrate_configuration"]["uuid"]
                        for dep in status["resources"]["deployment_list"]
                        if dep["uuid"]
                        == config["attrs_list"][0]["target_any_local_reference"]["uuid"]
                    ),
                    None,
                )
                api_filter = ""
                if substrate_id:
                    api_filter = "substrate_reference==" + substrate_id
                res, err = client.application.get_recovery_groups(app_id, api_filter)
                if err:
                    raise Exception("[{}] - {}".format(err["code"], err["error"]))
                action_args.append(
                    get_recovery_point_group_arg(
                        config, task["uuid"], res.json()["entities"]
                    )
                )

    app["spec"] = {
        "args": action_args,
        "target_kind": "Application",
        "target_uuid": app_id,
    }
    res, err = client.application.run_action(app_id, action_id, app)

    if err:
        raise Exception("[{}] - {}".format(err["code"], err["error"]))

    response = res.json()
    runlog_uuid = response["status"]["runlog_uuid"]
    click.echo(
        "Action is triggered. Got Action Runlog uuid: {}".format(
            highlight_text(runlog_uuid)
        )
    )

    if watch:

        def display_action(screen):
            screen.clear()
            screen.print_at(
                "Fetching runlog tree for action '{}'".format(action_name), 0, 0
            )
            screen.refresh()
            watch_action(
                runlog_uuid,
                app_name,
                get_api_client(),
                screen,
            )
            screen.wait_for_input(10.0)

        Display.wrapper(display_action, watch=True)

    else:
        click.echo("")
        click.echo(
            "# Hint1: You can run action in watch mode using: calm run action {} --app {} --watch".format(
                action_name, app_name
            )
        )
        click.echo(
            "# Hint2: You can watch action runlog on the app using: calm watch action_runlog {} --app {}".format(
                runlog_uuid, app_name
            )
        )


def poll_action(poll_func, completion_func, poll_interval=10):
    # Poll every 10 seconds on the app status, for 5 mins
    maxWait = 5 * 60
    count = 0
    while count < maxWait:
        # call status api
        res, err = poll_func()
        if err:
            raise Exception("[{}] - {}".format(err["code"], err["error"]))
        response = res.json()
        (completed, msg) = completion_func(response)
        if completed:
            # click.echo(msg)
            break
        count += poll_interval
        time.sleep(poll_interval)


def download_runlog(runlog_id, app_name, file_name):
    """Download runlogs, given runlog uuid and app name"""

    client = get_api_client()
    app = _get_app(client, app_name)
    app_id = app["metadata"]["uuid"]

    if not file_name:
        file_name = "runlog_{}.zip".format(runlog_id)

    res, err = client.application.download_runlog(app_id, runlog_id)
    if not err:
        with open(file_name, "wb") as fw:
            fw.write(res.content)
        click.echo("Runlogs saved as {}".format(highlight_text(file_name)))
    else:
        LOG.error("[{}] - {}".format(err["code"], err["error"]))
