from typing import Any, cast

from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from .common import UStreamerState
from jumpstarter.testing_strategies import ARBITRARY


class TestUStreamerStateRobustness:
    @given(
        data=st.fixed_dictionaries(
            {
                "ok": ARBITRARY,
                "result": ARBITRARY,
            }
        )
    )
    def test_constructor_never_crashes_on_arbitrary_dict(self, data: dict) -> None:
        try:
            cast(Any, UStreamerState)(**data)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"UStreamerState constructor crashed: {type(exc).__name__}: {exc}") from exc

    @given(ok=st.booleans())
    def test_constructor_with_valid_nested(self, ok: bool) -> None:
        try:
            state = cast(Any, UStreamerState)(
                ok=ok,
                result={
                    "encoder": {"type": "CPU", "quality": 80},
                    "source": {
                        "online": True,
                        "desired_fps": 30,
                        "captured_fps": 25,
                        "resolution": {"width": 1920, "height": 1080},
                    },
                },
            )
            assert isinstance(state.ok, bool)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"UStreamerState constructor crashed: {type(exc).__name__}: {exc}") from exc


class TestUStreamerEncoderRobustness:
    @given(enc_type=ARBITRARY, quality=ARBITRARY)
    def test_encoder_constructor_never_crashes(self, enc_type: object, quality: object) -> None:
        try:
            cast(Any, UStreamerState.Result.Encoder)(type=enc_type, quality=quality)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"Encoder constructor crashed: {type(exc).__name__}: {exc}") from exc


class TestUStreamerSourceRobustness:
    @given(
        online=ARBITRARY,
        desired_fps=ARBITRARY,
        captured_fps=ARBITRARY,
    )
    def test_source_constructor_never_crashes(self, online: object, desired_fps: object, captured_fps: object) -> None:
        try:
            cast(Any, UStreamerState.Result.Source)(
                online=online,
                desired_fps=desired_fps,
                captured_fps=captured_fps,
                resolution={"width": 100, "height": 100},
            )
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"Source constructor crashed: {type(exc).__name__}: {exc}") from exc


class TestUStreamerResolutionRobustness:
    @given(width=ARBITRARY, height=ARBITRARY)
    def test_resolution_constructor_never_crashes(self, width: object, height: object) -> None:
        try:
            cast(Any, UStreamerState.Result.Source.Resolution)(width=width, height=height)
        except (TypeError, ValueError, ValidationError):
            pass
        except Exception as exc:
            raise AssertionError(f"Resolution constructor crashed: {type(exc).__name__}: {exc}") from exc
