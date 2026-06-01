from typing import Any, cast

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .common import Capability, EntryMode, Metadata, PresignedRequest
from jumpstarter.testing_strategies import ARBITRARY


class TestEntryModeRobustness:
    @given(entry_is_file=ARBITRARY, entry_is_dir=ARBITRARY)
    def test_constructor_never_crashes_on_arbitrary(self, entry_is_file: object, entry_is_dir: object) -> None:
        try:
            mode = cast(Any, EntryMode)(entry_is_file=entry_is_file, entry_is_dir=entry_is_dir)
        except (TypeError, ValueError, ValidationError):
            return
        except Exception as exc:
            raise AssertionError(f"EntryMode constructor crashed: {type(exc).__name__}: {exc}") from exc
        assert isinstance(mode.entry_is_file, bool)
        assert isinstance(mode.entry_is_dir, bool)


class TestMetadataRobustness:
    @given(
        content_disposition=ARBITRARY,
        content_length=ARBITRARY,
        content_md5=ARBITRARY,
        content_type=ARBITRARY,
        etag=ARBITRARY,
    )
    def test_constructor_never_crashes_on_arbitrary(
        self,
        content_disposition: object,
        content_length: object,
        content_md5: object,
        content_type: object,
        etag: object,
    ) -> None:
        try:
            cast(Any, Metadata)(
                content_disposition=content_disposition,
                content_length=content_length,
                content_md5=content_md5,
                content_type=content_type,
                etag=etag,
                mode={"entry_is_file": True, "entry_is_dir": False},
            )
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"Metadata constructor crashed: {type(exc).__name__}: {exc}") from exc


class TestPresignedRequestRobustness:
    @given(url=ARBITRARY, method=ARBITRARY, headers=ARBITRARY)
    def test_constructor_never_crashes_on_arbitrary(self, url: object, method: object, headers: object) -> None:
        try:
            cast(Any, PresignedRequest)(url=url, method=method, headers=headers)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"PresignedRequest constructor crashed: {type(exc).__name__}: {exc}") from exc


class TestCapabilityRobustness:
    @given(
        data=st.fixed_dictionaries(
            {
                "stat": ARBITRARY,
                "stat_with_if_match": ARBITRARY,
                "stat_with_if_none_match": ARBITRARY,
                "read": ARBITRARY,
                "read_with_if_match": ARBITRARY,
                "read_with_if_none_match": ARBITRARY,
                "read_with_override_cache_control": ARBITRARY,
                "read_with_override_content_disposition": ARBITRARY,
                "read_with_override_content_type": ARBITRARY,
                "write": ARBITRARY,
                "write_can_multi": ARBITRARY,
                "write_can_empty": ARBITRARY,
                "write_can_append": ARBITRARY,
                "write_with_content_type": ARBITRARY,
                "write_with_content_disposition": ARBITRARY,
                "write_with_cache_control": ARBITRARY,
                "write_multi_max_size": ARBITRARY,
                "write_multi_min_size": ARBITRARY,
                "write_total_max_size": ARBITRARY,
                "create_dir": ARBITRARY,
                "delete": ARBITRARY,
                "copy": ARBITRARY,
                "rename": ARBITRARY,
                "list": ARBITRARY,
                "list_with_limit": ARBITRARY,
                "list_with_start_after": ARBITRARY,
                "list_with_recursive": ARBITRARY,
                "presign": ARBITRARY,
                "presign_read": ARBITRARY,
                "presign_stat": ARBITRARY,
                "presign_write": ARBITRARY,
                "shared": ARBITRARY,
                "blocking": ARBITRARY,
            }
        )
    )
    def test_constructor_never_crashes_on_arbitrary(self, data: dict) -> None:
        try:
            cast(Any, Capability)(**data)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"Capability constructor crashed: {type(exc).__name__}: {exc}") from exc
