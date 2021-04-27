import sys
import uuid
import click
import json
import time
import arrow
from prettytable import PrettyTable

from calm.dsl.config import get_context
from calm.dsl.api import get_api_client
from calm.dsl.builtins import create_environment_payload
from calm.dsl.builtins.models.helper.common import get_project
from calm.dsl.api import get_api_client
from calm.dsl.log import get_logging_handle

from .utils import (
    get_name_query,
    highlight_text,
)

LOG = get_logging_handle(__name__)


def create_environment(env_payload):

    client = get_api_client()

    env_payload.pop("status", None)

    # Pop default attribute from credentials
    for cred in env_payload["spec"]["resources"].get("credential_definition_list", []):
        cred.pop("default", None)

    # Adding uuid to creds and substrates
    for cred in env_payload["spec"]["resources"].get("credential_definition_list", []):
        cred["uuid"] = str(uuid.uuid4())

    for sub in env_payload["spec"]["resources"].get("substrate_definition_list", []):
        sub["uuid"] = str(uuid.uuid4())

    env_name = env_payload["spec"]["name"]
    LOG.info("Creating environment '{}'".format(env_name))
    res, err = client.environment.create(env_payload)
    if err:
        LOG.error(err)
        sys.exit(-1)

    res = res.json()
    env_uuid = res["metadata"]["uuid"]
    env_state = res["status"]["state"]
    LOG.info(
        "Environment '{}' created successfully. Environment state: '{}'".format(
            env_name, env_state
        )
    )

    stdout_dict = {"name": env_name, "uuid": env_uuid}

    return stdout_dict


def create_environment_from_dsl_class(env_class):

    env_payload = None

    infra = getattr(env_class, "providers", [])
    if not infra:
        LOG.warning(
            "From Calm v3.2, providers(infra) will be required to use environment for blueprints/marketplace usage"
        )

    UserEnvPayload, _ = create_environment_payload(env_class)
    env_payload = UserEnvPayload.get_dict()

    return create_environment(env_payload)


def get_project_environment(name=None, uuid=None, project_name=None, project_uuid=None):
    """Get project and environment with the given project and environment name or uuid. Raises exception if
    environment doesn't belong to the project"""

    client = get_api_client()
    project_data = get_project(project_name, project_uuid)
    project_uuid = project_data["metadata"]["uuid"]
    project_name = project_data["status"]["name"]
    environments = project_data["status"]["resources"]["environment_reference_list"]
    project_environments = {row["uuid"]: True for row in environments}

    if not name and not uuid:
        return None, project_data

    if uuid is None:
        params = {"filter": "name=={};project_reference=={}".format(name, project_uuid)}
        LOG.info(
            "Searching for the environment {} under project {}".format(
                name, project_name
            )
        )
        res, err = client.environment.list(params=params)
        if err:
            raise Exception("[{}] - {}".format(err["code"], err["error"]))

        response = res.json()
        entities = response.get("entities")
        if not entities:
            raise Exception(
                "No environment with name {} found in project {}".format(
                    name, project_name
                )
            )

        environment = entities[0]
        uuid = environment["metadata"]["uuid"]

    if not project_environments.get(uuid):
        raise Exception(
            "No environment with name {} found in project {}".format(name, project_name)
        )

    LOG.info("Environment {} found ".format(name))

    # for getting additional fields
    return get_environment_by_uuid(uuid), project_data


def get_environment_by_uuid(environment_uuid):
    """ Fetch the environment with the given name under the given project """
    LOG.info("Fetching details of environment (uuid='{}')".format(environment_uuid))
    client = get_api_client()
    res, err = client.environment.read(environment_uuid)
    if err:
        raise Exception("[{}] - {}".format(err["code"], err["error"]))

    environment = res.json()
    return environment


def get_environment_list(name, filter_by, limit, offset, quiet, out, project_name):
    """Get the environment, optionally filtered by a string"""

    client = get_api_client()

    params = {"length": limit, "offset": offset}
    filter_query = ""
    if name:
        filter_query = get_name_query([name])
    if filter_by:
        filter_query = filter_query + ";(" + filter_by + ")"
    if project_name:
        project_data = get_project(project_name)
        project_id = project_data["metadata"]["uuid"]
        filter_query = filter_query + ";project_reference=={}".format(project_id)
    if filter_query.startswith(";"):
        filter_query = filter_query[1:]

    if filter_query:
        params["filter"] = filter_query

    res, err = client.environment.list(params=params)

    if err:
        context = get_context()
        server_config = context.get_server_config()
        pc_ip = server_config["pc_ip"]

        LOG.warning("Cannot fetch environments from {}".format(pc_ip))
        return

    if out == "json":
        click.echo(json.dumps(res.json(), indent=4, separators=(",", ": ")))
        return

    json_rows = res.json()["entities"]
    if not json_rows:
        click.echo(highlight_text("No environment found !!!\n"))
        return

    if quiet:
        for _row in json_rows:
            row = _row["status"]
            click.echo(highlight_text(row["name"]))
        return

    table = PrettyTable()
    table.field_names = [
        "NAME",
        "PROJECT",
        "STATE",
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
                highlight_text(project),
                highlight_text(row["state"]),
                highlight_text(time.ctime(creation_time)),
                "{}".format(arrow.get(last_update_time).humanize()),
                highlight_text(row.get("uuid", "")),
            ]
        )
    click.echo(table)


def get_environment(environment_name, project_name):
    """returns the environment payload"""

    client = get_api_client()
    payload = {
        "length": 250,
        "offset": 0,
        "filter": "name=={}".format(environment_name),
    }

    if project_name:
        project = get_project(project_name)
        project_id = project["metadata"]["uuid"]
        payload["filter"] += ";project_reference=={}".format(project_id)

    res, err = client.environment.list(payload)
    if err:
        raise Exception("[{}] - {}".format(err["code"], err["error"]))

    res = res.json()
    if res["metadata"]["total_matches"] == 0:
        LOG.error("Environment '{}' not found".format(environment_name))
        sys.exit(-1)

    return res["entities"][0]


def delete_environment(environment_name, project_name):

    client = get_api_client()
    environment = get_environment(environment_name, project_name)
    environment_id = environment["metadata"]["uuid"]
    _, err = client.environment.delete(environment_id)
    if err:
        raise Exception("[{}] - {}".format(err["code"], err["error"]))
    LOG.info("Environment {} deleted".format(environment_name))
