"""Ephemeral image fetch and observation. No files, database, or media library."""

from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from dataclasses import dataclass, field
from ipaddress import ip_address
import logging
import os
import socket
from time import monotonic

import httpx

from evolving_companion.vision import (
    FETCH_TIMEOUT,
    MAX_IMAGE_BYTES,
    MAX_IMAGES,
    ImageData,
    OpenAICompatibleVisionProvider,
    VisualInput,
    VisualObservation,
    VisualUnavailable,
    VisionProvider,
    bounded_body,
    private_http,
    quiet_client_logs,
    vision_settings,
)

DEFAULT_MEDIA_HOSTS = (
    "gchat.qpic.cn",
    "c2cpicdw.qpic.cn",
    "multimedia.nt.qq.com.cn",
    "multimedia.nt.qq.com",
)


@dataclass(frozen=True)
class MediaReference:
    url: str | None = field(default=None, repr=False)


def public_addresses(host: str) -> Sequence[str]:
    return tuple(
        {
            entry[4][0]
            for entry in socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
        }
    )


def image_type(content: bytes) -> str:
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        if b"ANIM" in content or (
            content[12:16] == b"VP8X" and len(content) > 20 and content[20] & 2
        ):
            raise ValueError("Animated images are unsupported")
        return "image/webp"
    raise ValueError("Unsupported image type")


class MediaFetcher:
    def __init__(
        self,
        *,
        allowed_hosts: Sequence[str] = DEFAULT_MEDIA_HOSTS,
        resolver: Callable[[str], Sequence[str]] = public_addresses,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._hosts = frozenset(host.lower() for host in allowed_hosts)
        self._resolver = resolver
        quiet_client_logs()
        self._client = httpx.Client(
            timeout=httpx.Timeout(FETCH_TIMEOUT, connect=5),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    def fetch(self, reference: MediaReference) -> ImageData:
        url = httpx.URL(reference.url or "")
        if (
            url.scheme != "https"
            or url.host not in self._hosts
            or url.userinfo
            or url.fragment
            or url.port not in (None, 443)
        ):
            raise ValueError("Unsupported media source")
        addresses = self._resolver(url.host)
        if not addresses or any(
            not ip_address(address).is_global for address in addresses
        ):
            raise ValueError("Non-public media source")
        # Pin the checked IP; preserve Host and TLS SNI so a second DNS resolution
        # cannot rebind a public URL to localhost or a private network.
        pinned = url.copy_with(host=addresses[0])
        with (
            private_http(),
            self._client.stream(
                "GET",
                pinned,
                headers={"Host": url.host, "Accept-Encoding": "identity"},
                extensions={"sni_hostname": url.host},
            ) as response,
        ):
            content = bounded_body(
                response, MAX_IMAGE_BYTES, monotonic() + FETCH_TIMEOUT
            )
            mime = image_type(content)
            declared = response.headers.get("content-type", "").split(";")[0].lower()
            if declared != mime:
                raise ValueError("MIME does not match image type")
        return ImageData(content, mime)

    def close(self) -> None:
        self._client.close()


class VisionInputService:
    def __init__(
        self,
        provider: VisionProvider | None = None,
        fetcher: MediaFetcher | None = None,
    ):
        self.provider, self.fetcher = provider, fetcher

    def observe(
        self, references: Sequence[MediaReference], user_text: str
    ) -> tuple[VisualInput, ...]:
        results: list[VisualInput] = []
        for reference in references[:MAX_IMAGES]:
            if self.provider is None or self.fetcher is None:
                results.append(VisualUnavailable("disabled"))
                continue
            started = monotonic()
            try:
                image = self.fetcher.fetch(reference)
            except Exception:
                results.append(VisualUnavailable("media_unavailable"))
            else:
                try:
                    observation = self.provider.analyze_image(image, user_text)
                    if not isinstance(observation, VisualObservation):
                        raise ValueError("Invalid observation")
                    results.append(observation)
                except Exception:
                    results.append(VisualUnavailable("analysis_unavailable"))
            logging.getLogger(__name__).info(
                "media observation status=%s seconds=%.3f",
                "available"
                if isinstance(results[-1], VisualObservation)
                else "unavailable",
                monotonic() - started,
            )
        return tuple(results)


def create_vision_input(
    resources: ExitStack, environment: Mapping[str, str] | None = None
) -> VisionInputService:
    values = os.environ if environment is None else environment
    settings = vision_settings(values)
    if settings is None:
        return VisionInputService()
    provider = OpenAICompatibleVisionProvider(*settings)
    resources.callback(provider.close)
    hosts = tuple(
        item.strip().lower()
        for item in values.get(
            "SI_VISION_MEDIA_HOSTS", ",".join(DEFAULT_MEDIA_HOSTS)
        ).split(",")
        if item.strip()
    )
    fetcher = MediaFetcher(allowed_hosts=hosts)
    resources.callback(fetcher.close)
    return VisionInputService(provider, fetcher)
