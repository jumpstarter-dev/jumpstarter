//! Endpoint + IP configuration for the operator install (`cluster/endpoints.py`
//! + `jumpstarter.common.ipaddr`).

use crate::command::run_command;
use crate::common::{GRPC_NODEPORT, KIND_GRPC_HOST_PORT, KIND_ROUTER_HOST_PORT, ROUTER_NODEPORT};
use crate::error::{ClusterError, Result};
use crate::minikube::minikube_installed;

/// The host's outbound IPv4 address (`get_ip_address`): connect a UDP socket to a
/// well-known address (no packets sent) and read the chosen local interface.
pub fn get_ip_address() -> String {
    use std::net::UdpSocket;
    if let Ok(sock) = UdpSocket::bind("0.0.0.0:0") {
        if sock.connect("192.175.48.1:53").is_ok() {
            if let Ok(local) = sock.local_addr() {
                let ip = local.ip();
                if !ip.is_loopback() && !ip.is_unspecified() {
                    return ip.to_string();
                }
            }
        }
    }
    "0.0.0.0".to_string()
}

/// A minikube profile's IP (`minikube ip -p <profile>`).
pub async fn get_minikube_ip(profile: &str, minikube: &str) -> Result<String> {
    let out = run_command(&[minikube, "ip", "-p", profile]).await?;
    if out.ok() {
        Ok(out.stdout)
    } else {
        Err(ClusterError::Endpoint(out.stderr))
    }
}

/// Resolve the cluster IP for endpoint config (`get_ip_generic`).
pub async fn get_ip_generic(
    cluster_type: Option<&str>,
    minikube: &str,
    cluster_name: &str,
) -> Result<String> {
    if cluster_type == Some("minikube") {
        if !minikube_installed(minikube) {
            return Err(ClusterError::tool_not_installed("minikube"));
        }
        get_minikube_ip(cluster_name, minikube).await.map_err(|e| {
            ClusterError::Endpoint(format!("Could not determine Minikube IP address.\n{e}"))
        })
    } else {
        let ip = get_ip_address();
        if ip == "0.0.0.0" {
            return Err(ClusterError::Endpoint(
                "Could not determine IP address, use --ip <IP> to specify an IP address"
                    .to_string(),
            ));
        }
        Ok(ip)
    }
}

/// Fill in the ip / basedomain / grpc / router endpoints, defaulting from the IP
/// (`configure_endpoints`). Returns `(ip, basedomain, grpc, router)`.
#[allow(clippy::too_many_arguments)]
pub async fn configure_endpoints(
    cluster_type: Option<&str>,
    minikube: &str,
    cluster_name: &str,
    ip: Option<String>,
    basedomain: Option<String>,
    grpc_endpoint: Option<String>,
    router_endpoint: Option<String>,
) -> Result<(String, String, String, String)> {
    let ip = match ip {
        Some(ip) => ip,
        None => get_ip_generic(cluster_type, minikube, cluster_name).await?,
    };
    let basedomain = basedomain.unwrap_or_else(|| format!("jumpstarter.{ip}.nip.io"));
    let grpc_endpoint = grpc_endpoint.unwrap_or_else(|| {
        let port = if cluster_type == Some("k3s") {
            GRPC_NODEPORT
        } else {
            KIND_GRPC_HOST_PORT
        };
        format!("grpc.{basedomain}:{port}")
    });
    let router_endpoint = router_endpoint.unwrap_or_else(|| {
        let port = if cluster_type == Some("k3s") {
            ROUTER_NODEPORT
        } else {
            KIND_ROUTER_HOST_PORT
        };
        format!("router.{basedomain}:{port}")
    });
    Ok((ip, basedomain, grpc_endpoint, router_endpoint))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn configures_defaults_from_ip() {
        let (ip, base, grpc, router) = configure_endpoints(
            Some("kind"),
            "minikube",
            "c",
            Some("1.2.3.4".to_string()),
            None,
            None,
            None,
        )
        .await
        .unwrap();
        assert_eq!(ip, "1.2.3.4");
        assert_eq!(base, "jumpstarter.1.2.3.4.nip.io");
        assert_eq!(grpc, "grpc.jumpstarter.1.2.3.4.nip.io:8082");
        assert_eq!(router, "router.jumpstarter.1.2.3.4.nip.io:8083");
    }

    #[tokio::test]
    async fn k3s_uses_nodeports() {
        let (_, _, grpc, router) = configure_endpoints(
            Some("k3s"),
            "minikube",
            "c",
            Some("10.0.0.1".to_string()),
            None,
            None,
            None,
        )
        .await
        .unwrap();
        assert_eq!(grpc, "grpc.jumpstarter.10.0.0.1.nip.io:30010");
        assert_eq!(router, "router.jumpstarter.10.0.0.1.nip.io:30011");
    }
}
