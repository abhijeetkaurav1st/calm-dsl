from calm.dsl.builtins import Account, AhvAccountSpec
from calm.dsl.constants import PROVIDER


class AhvSpec(AhvAccountSpec):

    server = "ahv_pc_ip"
    username = "username"
    password = "password"
    port = 9440


class AhvAccount(Account):

    provider_type = PROVIDER.ACCOUNT.NUTANIX
    spec = AhvSpec


print(AhvAccount.json_dumps(pprint=True))
