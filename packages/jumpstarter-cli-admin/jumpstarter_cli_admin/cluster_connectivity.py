import asyncio
from typing import Optional, Tuple

import grpc
from grpc import ChannelConnectivity


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


async def check_controller_connectivity(endpoint: str, timeout: float = 5.0) -> Tuple[bool, Optional[str]]:
    """Test connectivity to Jumpstarter controller gRPC service.

    Args:
        endpoint: Controller gRPC endpoint
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (reachable: bool, error_message: Optional[str])
    """
    return await check_grpc_connectivity(endpoint, timeout)


async def check_router_connectivity(endpoint: str, timeout: float = 5.0) -> Tuple[bool, Optional[str]]:
    """Test connectivity to Jumpstarter router gRPC service.

    Args:
        endpoint: Router gRPC endpoint
        timeout: Connection timeout in seconds

    Returns:
        Tuple of (reachable: bool, error_message: Optional[str])
    """
    return await check_grpc_connectivity(endpoint, timeout)
