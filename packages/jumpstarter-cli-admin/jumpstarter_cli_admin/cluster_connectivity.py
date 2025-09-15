import asyncio
from typing import Optional, Tuple

import aiohttp
import grpc
from grpc import ChannelConnectivity
from grpc_reflection.v1alpha import reflection_pb2, reflection_pb2_grpc


async def check_grpc_connectivity(endpoint: str, timeout: float = 5.0) -> Tuple[bool, Optional[str]]:
    """Test gRPC connectivity to an endpoint.

    Args:
        endpoint: gRPC endpoint (e.g., 'grpc.example.com:8082')
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (reachable: bool, error_message: Optional[str])
    """
    try:
        # Create an insecure channel
        channel = grpc.aio.insecure_channel(endpoint)

        # Try to connect with timeout
        try:
            await asyncio.wait_for(channel.channel_ready(), timeout=timeout)

            # Check if channel is ready
            state = channel.get_state(try_to_connect=True)
            if state == ChannelConnectivity.READY:
                await channel.close()
                return True, None
            else:
                await channel.close()
                return False, f"Channel state: {state.name}"

        except asyncio.TimeoutError:
            await channel.close()
            return False, f"Connection timeout after {timeout}s"

    except Exception as e:
        return False, f"Connection failed: {str(e)}"


async def check_grpc_service_with_reflection(endpoint: str, timeout: float = 5.0) -> Tuple[bool, Optional[str]]:
    """Test gRPC service availability using reflection API.

    Args:
        endpoint: gRPC endpoint (e.g., 'grpc.example.com:8082')
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (reachable: bool, error_message: Optional[str])
    """
    channel = None
    try:
        # Create an insecure channel
        channel = grpc.aio.insecure_channel(endpoint)

        # Create reflection stub
        reflection_stub = reflection_pb2_grpc.ServerReflectionStub(channel)

        # Create a request to list services
        request = reflection_pb2.ServerReflectionRequest(
            list_services=""
        )

        # Send the request with timeout
        response_iterator = reflection_stub.ServerReflectionInfo([request], timeout=timeout)

        # Try to get the first response
        response = await response_iterator.__anext__()

        # Check if we got a valid response
        if response.HasField('list_services_response'):
            services = response.list_services_response.service
            # Check if jumpstarter services are present
            jumpstarter_services = [s for s in services if 'jumpstarter' in s.name.lower()]
            if jumpstarter_services:
                await channel.close()
                return True, None
            else:
                await channel.close()
                return False, "No Jumpstarter services found in reflection response"
        else:
            await channel.close()
            return False, "Invalid reflection response"

    except asyncio.TimeoutError:
        if channel:
            await channel.close()
        return False, f"Service reflection timeout after {timeout}s"
    except grpc.RpcError as e:
        if channel:
            await channel.close()
        # If reflection is not available, it might still be a valid service
        if e.code() == grpc.StatusCode.UNIMPLEMENTED:
            return False, "gRPC reflection not supported by service"
        else:
            return False, f"gRPC error: {e.code().name} - {e.details()}"
    except Exception as e:
        if channel:
            await channel.close()
        return False, f"Service check failed: {str(e)}"


async def check_controller_connectivity(endpoint: str, timeout: float = 5.0) -> Tuple[bool, Optional[str]]:
    """Test connectivity to Jumpstarter controller gRPC service.

    Args:
        endpoint: Controller gRPC endpoint
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (reachable: bool, error_message: Optional[str])
    """
    # First try with reflection API to verify the service is actually running
    reachable, error = await check_grpc_service_with_reflection(endpoint, timeout)

    if reachable:
        return True, None

    # If reflection failed but not because it's unimplemented, try basic connectivity
    if error and "reflection not supported" in error:
        # Fall back to basic connectivity check
        basic_reachable, basic_error = await check_grpc_connectivity(endpoint, timeout)
        if basic_reachable:
            return True, None
        else:
            # Port might be open but service not running properly
            return False, f"gRPC port reachable but service not responding properly: {basic_error}"

    # Check if it's a network connectivity issue
    if error and any(msg in error.lower() for msg in ["timeout", "connection failed", "unavailable"]):
        # Try HTTP endpoints to see if the host is reachable at all
        host = endpoint.split(':')[0] if ':' in endpoint else endpoint
        http_reachable, _ = await check_controller_health_endpoints(host, timeout/2)

        if http_reachable:
            return False, (f"Controller host is reachable (HTTP endpoints responding) but "
                         f"gRPC service on {endpoint} is not available: {error}")
        else:
            return False, f"Cannot reach controller at {endpoint} - appears to be a network connectivity issue: {error}"

    # Service is reachable but not functioning properly
    if error and "no jumpstarter services found" in error.lower():
        return False, f"gRPC service is running on {endpoint} but Jumpstarter controller services are not available"

    # Return the specific error
    return False, f"Controller service check failed: {error}"


async def check_http_endpoint(url: str, timeout: float = 5.0) -> Tuple[bool, Optional[str]]:
    """Check if an HTTP endpoint is reachable.

    Args:
        url: HTTP URL to check
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (reachable: bool, error_message: Optional[str])
    """
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=timeout), ssl=False) as response:
                if response.status < 500:
                    return True, None
                else:
                    return False, f"HTTP {response.status}: {response.reason}"
    except asyncio.TimeoutError:
        return False, f"HTTP request timeout after {timeout}s"
    except aiohttp.ClientError as e:
        return False, f"HTTP request failed: {str(e)}"
    except Exception as e:
        return False, f"Unexpected error: {str(e)}"


async def check_controller_health_endpoints(base_url: str, timeout: float = 5.0) -> Tuple[bool, Optional[str]]:
    """Check Jumpstarter controller health endpoints.

    Args:
        base_url: Base URL of the controller (e.g., 'http://grpc.example.com')
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (reachable: bool, error_message: Optional[str])
    """
    # Extract host from gRPC endpoint if needed
    if ':' in base_url and not base_url.startswith('http'):
        host = base_url.split(':')[0]
        base_url = f"http://{host}"

    # Check dashboard endpoint (port 8084)
    dashboard_url = f"{base_url}:8084/"
    dashboard_reachable, dashboard_error = await check_http_endpoint(dashboard_url, timeout)

    if dashboard_reachable:
        return True, None

    # Check health probe endpoint (port 8081)
    health_url = f"{base_url}:8081/healthz"
    health_reachable, health_error = await check_http_endpoint(health_url, timeout)

    if health_reachable:
        return True, None

    # Both endpoints failed
    return False, f"Dashboard: {dashboard_error}, Health: {health_error}"


async def check_router_connectivity(endpoint: str, timeout: float = 5.0) -> Tuple[bool, Optional[str]]:
    """Test connectivity to Jumpstarter router gRPC service.

    Args:
        endpoint: Router gRPC endpoint
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (reachable: bool, error_message: Optional[str])
    """
    # For router, we can only do basic connectivity check as it requires auth for all operations
    reachable, error = await check_grpc_connectivity(endpoint, timeout)

    if reachable:
        return True, None

    # Provide more context about the error
    if error and "timeout" in error.lower():
        return False, (f"Router service at {endpoint} is not responding (timeout) - "
                      f"check if the router is running and the endpoint is correct")
    elif error and "connection failed" in error.lower():
        return False, f"Cannot connect to router at {endpoint} - check network connectivity and firewall rules"
    else:
        return False, f"Router connectivity check failed: {error}"
