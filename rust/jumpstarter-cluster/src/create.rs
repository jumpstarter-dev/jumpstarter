//! `create_cluster_and_install` (`cluster/operations.py`): create the local
//! cluster (kind/minikube) then optionally install the Jumpstarter operator.
//!
//! k3s (remote SSH provisioning) is not ported — it is opt-in, requires a live
//! Linux host, and cannot run in CI; `--k3s` returns a clear error.

use crate::common::validate_cluster_name;
use crate::endpoints::configure_endpoints;
use crate::error::{ClusterError, Result};
use crate::kind::create_kind_cluster_with_options;
use crate::minikube::create_minikube_cluster_with_options;
use crate::operator::install_jumpstarter_operator;
use crate::progress::Progress;

/// Options for [`create_cluster_and_install`].
pub struct CreateOptions<'a> {
    pub cluster_type: &'a str,
    pub force_recreate: bool,
    pub cluster_name: &'a str,
    pub kind_extra_args: &'a str,
    pub minikube_extra_args: &'a str,
    pub kind: &'a str,
    pub minikube: &'a str,
    pub extra_certs: Option<&'a str>,
    pub install_jumpstarter: bool,
    pub namespace: &'a str,
    pub version: Option<&'a str>,
    pub kubeconfig: Option<&'a str>,
    pub context: Option<&'a str>,
    pub ip: Option<String>,
    pub basedomain: Option<String>,
    pub grpc_endpoint: Option<String>,
    pub router_endpoint: Option<String>,
    pub operator_installer: Option<&'a str>,
    pub k3s_ssh_host: Option<&'a str>,
}

pub async fn create_cluster_and_install(opts: CreateOptions<'_>, progress: &dyn Progress) -> Result<()> {
    let cluster_name = validate_cluster_name(opts.cluster_name)?;

    if opts.force_recreate {
        progress
            .warning(&format!("WARNING: Force recreating cluster \"{cluster_name}\" will destroy ALL data in it!"));
        if !progress.confirm(&format!("Are you sure you want to recreate cluster \"{cluster_name}\"?")) {
            progress.progress("Cluster recreation cancelled.");
            return Err(ClusterError::Cancelled);
        }
    }

    match opts.cluster_type {
        "kind" => {
            create_kind_cluster_with_options(
                opts.kind,
                &cluster_name,
                opts.kind_extra_args,
                opts.force_recreate,
                opts.extra_certs,
                progress,
            )
            .await?
        }
        "minikube" => {
            create_minikube_cluster_with_options(
                opts.minikube,
                &cluster_name,
                opts.minikube_extra_args,
                opts.force_recreate,
                opts.extra_certs,
                progress,
            )
            .await?
        }
        "k3s" => {
            return Err(ClusterError::Operation(
                "k3s cluster provisioning is not available; use --kind or --minikube".to_string(),
            ));
        }
        other => {
            return Err(ClusterError::Validation(format!("Unsupported cluster type: {other}")));
        }
    }

    if opts.install_jumpstarter {
        let (_, basedomain, grpc, router) = configure_endpoints(
            Some(opts.cluster_type),
            opts.minikube,
            &cluster_name,
            opts.ip.clone(),
            opts.basedomain.clone(),
            opts.grpc_endpoint.clone(),
            opts.router_endpoint.clone(),
        )
        .await?;

        let version = opts
            .version
            .ok_or_else(|| ClusterError::Operation("Version must be specified when installing Jumpstarter".to_string()))?;

        install_jumpstarter_operator(
            version,
            opts.namespace,
            &basedomain,
            &grpc,
            &router,
            opts.kubeconfig,
            opts.context,
            opts.operator_installer,
            progress,
        )
        .await?;
    }

    Ok(())
}
