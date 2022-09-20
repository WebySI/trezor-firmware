"""
Implementation of Orchard key derivation scheme
for deterministic wallets according to the ZIP-32.

see: https://zips.z.cash/zip-0032
"""

from typing import TYPE_CHECKING

from trezor.crypto.hashlib import blake2b

from apps.bitcoin.keychain import get_coin_by_name
from apps.common.keychain import Keychain
from apps.common.paths import PathSchema
from apps.common.seed import get_seed

from .crypto.keys import FullViewingKey, sk_to_ask
from .crypto.utils import i2leosp, prf_expand

if TYPE_CHECKING:
    from apps.common.coininfo import CoinInfo
    from apps.common.paths import Bip32Path
    from trezor.wire import Context
    from trezor.crypto.pallas import Scalar
    from typing import Callable, TypeVar, Awaitable
    from typing_extensions import Protocol

    class MsgWithCoinNameType(Protocol):
        coin_name: str

    MsgIn = TypeVar("MsgIn", bound=MsgWithCoinNameType)
    Result = TypeVar("Result")


PATTERN_ZIP32 = "m/32'/coin_type'/account'"


class ExtendedSpendingKey:
    def __init__(self, sk: bytes, c: bytes) -> None:
        self.sk = sk  # spending key
        self.c = c  # chain code

    def spending_key(self) -> bytes:
        return self.sk

    def full_viewing_key(self) -> FullViewingKey:
        return FullViewingKey.from_spending_key(self.sk)

    def spend_authorizing_key(self) -> Scalar:
        return sk_to_ask(self.sk)

    @staticmethod
    def get_master(seed: bytes) -> "ExtendedSpendingKey":
        """Generates the Orchard master ExtendedSpendingKey from `seed`."""
        I = blake2b(personal=b"ZcashIP32Orchard", data=seed).digest()
        return ExtendedSpendingKey(sk=I[:32], c=I[32:])

    # apps.common.keychain.NodeProtocol methods:

    def derive_path(self, path: Bip32Path) -> None:
        """Derives a descendant ExtendedSpendingKey according to the `path`."""
        for i in path:
            assert i >= 1 << 31
            I = prf_expand(self.c, bytes([0x81]) + self.sk + i2leosp(32, i))
            self.sk, self.c = I[:32], I[32:]

    def clone(self) -> "ExtendedSpendingKey":
        return ExtendedSpendingKey(self.sk, self.c)

    def __del__(self):
        del self.sk
        del self.c


class OrchardKeychain(Keychain):
    def __init__(self, seed: bytes, coin: CoinInfo) -> None:
        schema = PathSchema.parse(PATTERN_ZIP32, (coin.slip44,))
        super().__init__(seed, "pallas", [schema], [[b"Zcash Orchard"]])

    @staticmethod
    async def for_coin(ctx: Context, coin: CoinInfo) -> "OrchardKeychain":
        seed = await get_seed(ctx)
        return OrchardKeychain(seed, coin)

    def derive(self, path: Bip32Path) -> ExtendedSpendingKey:
        self.verify_path(path)
        return self._derive_with_cache(
            prefix_len=3,
            path=path,
            new_root=lambda: ExtendedSpendingKey.get_master(self.seed),
        )

    def root_fingerprint(self) -> int:
        raise NotImplementedError


def with_keychain(
    func: Callable[[Context, MsgIn, OrchardKeychain], Awaitable[Result]]
) -> Callable[[Context, MsgIn], Awaitable[Result]]:
    async def wrapper(ctx: Context, msg: MsgIn):
        coin = get_coin_by_name(msg.coin_name)
        keychain = await OrchardKeychain.for_coin(ctx, coin)
        return await func(ctx, msg, keychain)

    return wrapper
