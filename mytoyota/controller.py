"""Toyota Connected Services Controller """

from datetime import datetime
import logging
from typing import Optional, Union

import httpx

from mytoyota.const import (
    BASE_HEADERS,
    CUSTOMERPROFILE,
    ENDPOINT_AUTH,
    HTTP_INTERNAL,
    HTTP_NO_CONTENT,
    HTTP_OK,
    HTTP_SERVICE_UNAVAILABLE,
    PASSWORD,
    SUPPORTED_REGIONS,
    TIMEOUT,
    TOKEN,
    TOKEN_DURATION,
    TOKEN_VALID_URL,
    USERNAME,
    UUID,
)
from mytoyota.exceptions import ToyotaApiError, ToyotaInternalError, ToyotaLoginError
from mytoyota.utils import is_valid_token

_LOGGER: logging.Logger = logging.getLogger(__package__)


class Controller:
    """Httpx async client controller class"""

    _token: str = None
    _token_expiration: datetime = None

    def __init__(  # pylint: disable=too-many-arguments
        self,
        locale: str,
        region: str,
        username: str,
        password: str,
        uuid: str = None,
    ) -> None:
        self._locale = locale
        self._region = region
        self._username = username
        self._password = password
        self._uuid = uuid

    def _build_auth_body(self) -> dict:
        """Return auth body in a dict"""
        return {USERNAME: self._username, PASSWORD: self._password}

    def _get_auth_endpoint(self) -> str:
        """Returns auth endpoint"""
        return SUPPORTED_REGIONS[self._region][ENDPOINT_AUTH]

    def _get_auth_valid_endpoint(self) -> str:
        """Returns token is valid endpoint"""
        return SUPPORTED_REGIONS[self._region][TOKEN_VALID_URL]

    async def get_uuid(self) -> str:
        """Returns uuid"""
        return self._uuid

    async def first_login(self) -> None:
        """Perform first login"""
        await self._update_token()

    @staticmethod
    def _has_expired(creation_dt: datetime, duration: int) -> bool:
        """Checks if an specified token/object has expired"""
        return datetime.now().timestamp() - creation_dt.timestamp() > duration

    async def _update_token(self) -> None:
        """Performs login to toyota servers and retrieves token and uuid for the account."""

        # Cannot authenticate with aiohttp (returns 415),
        # but it works with httpx.
        async with httpx.AsyncClient() as client:
            response = await client.post(
                self._get_auth_endpoint(),
                headers={"X-TME-LC": self._locale},
                json=self._build_auth_body(),
            )
            if response.status_code == HTTP_OK:
                result = response.json()

                if TOKEN not in result or UUID not in result[CUSTOMERPROFILE]:
                    raise ToyotaLoginError("Could not get token or UUID from result")

                token = result.get(TOKEN)
                uuid = result[CUSTOMERPROFILE][UUID]

                if is_valid_token(token):
                    self._uuid = uuid
                    self._token = token
                    self._token_expiration = datetime.now()
            else:
                raise ToyotaLoginError(
                    "Login failed, check your credentials! {}".format(response.text)
                )

    async def _is_token_valid(self) -> bool:
        """Checks if token is valid"""

        async with httpx.AsyncClient() as client:
            response = await client.post(
                self._get_auth_valid_endpoint(),
                json={TOKEN: self._token},
            )
            if response.status_code == HTTP_OK:
                result = response.json()

                if result["valid"]:
                    return True
                return False

            raise ToyotaLoginError(
                "Error when trying to check token: {}".format(response.text)
            )

    async def request(  # pylint: disable=too-many-arguments
        self,
        method: str,
        endpoint: str,
        base_url: Optional[str] = None,
        body: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ) -> Union[dict, list, None]:
        """Shared request method"""

        if headers is None:
            headers = {}

        if method not in ("GET", "POST", "PUT", "DELETE"):
            raise ToyotaInternalError("Invalid request method provided")

        if not self._token or self._has_expired(self._token_expiration, TOKEN_DURATION):
            if not await self._is_token_valid():
                await self._update_token()

        if base_url:
            url = SUPPORTED_REGIONS[self._region][base_url] + endpoint
        else:
            url = endpoint

        headers.update(
            {
                "X-TME-LC": self._locale,
                "X-TME-LOCALE": self._locale,
                "X-TME-TOKEN": self._token,
            }
        )

        if method in ("GET", "POST"):
            headers.update(
                {
                    "Cookie": f"iPlanetDirectoryPro={self._token}",
                    "uuid": self._uuid,
                }
            )

        # Cannot authenticate with aiohttp (returns 415),
        # but it works with httpx.
        async with httpx.AsyncClient(headers=BASE_HEADERS, timeout=TIMEOUT) as client:
            response = await client.request(
                method, url, headers=headers, json=body, params=params
            )
            if response.status_code == HTTP_OK:
                result = response.json()
            elif response.status_code == HTTP_NO_CONTENT:
                # This prevents raising or logging an error
                # if the user have not setup Connected Services
                result = None
                _LOGGER.debug("Connected services is disabled")
            elif response.status_code == HTTP_INTERNAL:
                response = response.json()
                raise ToyotaInternalError(
                    "Internal server error occurred! Code: "
                    + response["code"]
                    + " - "
                    + response["message"],
                )
            elif response.status_code == HTTP_SERVICE_UNAVAILABLE:
                raise ToyotaApiError(
                    "Toyota Connected Services are temporarily unavailable"
                )
            else:
                raise ToyotaInternalError(
                    "HTTP: " + str(response.status_code) + " - " + response.text
                )

        return result