from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .bundle import (
    Dtb,
    DtbVariant,
    FileAddress,
    FlashBundleSpecV1Alpha1,
    FlasherBundleManifestV1Alpha1,
    FlasherLogin,
    ObjectMeta,
)
from jumpstarter.testing_strategies import ARBITRARY


class TestFileAddressRobustness:
    @given(file=ARBITRARY, address=ARBITRARY)
    def test_constructor_never_crashes(self, file: object, address: object) -> None:
        try:
            FileAddress(file=file, address=address)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"FileAddress crashed: {type(exc).__name__}: {exc}") from exc


class TestDtbVariantRobustness:
    @given(bootcmd=ARBITRARY, file=ARBITRARY)
    def test_constructor_never_crashes(self, bootcmd: object, file: object) -> None:
        try:
            DtbVariant(bootcmd=bootcmd, file=file)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"DtbVariant crashed: {type(exc).__name__}: {exc}") from exc


class TestDtbRobustness:
    @given(default=ARBITRARY, address=ARBITRARY, variants=ARBITRARY)
    def test_constructor_never_crashes(self, default: object, address: object, variants: object) -> None:
        try:
            Dtb(default=default, address=address, variants=variants)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"Dtb crashed: {type(exc).__name__}: {exc}") from exc


class TestFlasherLoginRobustness:
    @given(login_prompt=ARBITRARY, username=ARBITRARY, password=ARBITRARY, prompt=ARBITRARY)
    def test_constructor_never_crashes(
        self, login_prompt: object, username: object, password: object, prompt: object
    ) -> None:
        try:
            FlasherLogin(login_prompt=login_prompt, username=username, password=password, prompt=prompt)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"FlasherLogin crashed: {type(exc).__name__}: {exc}") from exc


class TestFlashBundleSpecV1Alpha1Robustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            FlashBundleSpecV1Alpha1(**kwargs)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"FlashBundleSpecV1Alpha1 crashed: {type(exc).__name__}: {exc}") from exc


class TestObjectMetaRobustness:
    @given(name=ARBITRARY)
    def test_constructor_never_crashes(self, name: object) -> None:
        try:
            ObjectMeta(name=name)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"ObjectMeta crashed: {type(exc).__name__}: {exc}") from exc


class TestFlasherBundleManifestV1Alpha1Robustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(self, kwargs: dict) -> None:
        try:
            FlasherBundleManifestV1Alpha1(**kwargs)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"FlasherBundleManifestV1Alpha1 crashed: {type(exc).__name__}: {exc}") from exc
