//! The real [`LeaseProvider`] over `jumpstarter.client.v1.ClientService`
//! (`python/.../client/grpc.py:381-546`).

use std::time::Duration;

use jumpstarter_config::ClientConfig;
use jumpstarter_protocol::client_v1;
use jumpstarter_protocol::client_v1::client_service_client::ClientServiceClient;
use jumpstarter_protocol::v1::controller_service_client::ControllerServiceClient;
use jumpstarter_protocol::v1::{DialRequest, DialResponse};
use tonic::service::interceptor::InterceptedService;
use tonic::transport::Channel;

use crate::channel::{self, AuthInterceptor};
use crate::dial::dial_with_retry;
use crate::error::ClientError;
use crate::lease::{CreateLeaseParams, LeaseProvider, LeaseView};

type Svc = InterceptedService<Channel, AuthInterceptor>;

/// A connected client to the controller's `ClientService` and `ControllerService`.
#[derive(Clone)]
pub struct ControllerClient {
    namespace: String,
    client: ClientServiceClient<Svc>,
    controller: ControllerServiceClient<Svc>,
}

impl ControllerClient {
    /// Connect and authenticate using a client config.
    pub async fn connect(config: &ClientConfig) -> Result<Self, ClientError> {
        let svc = channel::connect(config).await?;
        Ok(Self {
            namespace: config.metadata.namespace.clone().unwrap_or_default(),
            client: ClientServiceClient::new(svc.clone()),
            controller: ControllerServiceClient::new(svc),
        })
    }

    fn lease_path(&self, name: &str) -> String {
        format!("namespaces/{}/leases/{}", self.namespace, name)
    }

    /// Dial the exporter behind a lease, returning router rendezvous details.
    /// Retries the transient "exporter not ready" race under `dial_timeout`.
    pub async fn dial(
        &self,
        lease_name: &str,
        dial_timeout: Duration,
    ) -> Result<DialResponse, ClientError> {
        let lease_name = lease_name.to_string();
        dial_with_retry(dial_timeout, || {
            let mut controller = self.controller.clone();
            let req = DialRequest {
                lease_name: lease_name.clone(),
            };
            async move { controller.dial(req).await.map(|r| r.into_inner()) }
        })
        .await
    }
}

/// The final segment of a resource identifier (`namespaces/ns/leases/X` -> `X`).
fn last_segment(identifier: &str) -> String {
    identifier
        .rsplit('/')
        .next()
        .unwrap_or(identifier)
        .to_string()
}

fn to_proto_duration(d: std::time::Duration) -> prost_types::Duration {
    prost_types::Duration {
        seconds: d.as_secs() as i64,
        nanos: d.subsec_nanos() as i32,
    }
}

fn to_view(lease: client_v1::Lease) -> LeaseView {
    LeaseView {
        name: last_segment(&lease.name),
        client: lease
            .client
            .as_deref()
            .map(last_segment)
            .unwrap_or_default(),
        selector: lease.selector,
        exporter: lease
            .exporter
            .as_deref()
            .map(last_segment)
            .unwrap_or_default(),
        conditions: lease.conditions,
    }
}

impl LeaseProvider for ControllerClient {
    async fn create_lease(&self, params: &CreateLeaseParams) -> Result<String, ClientError> {
        let lease = client_v1::Lease {
            duration: Some(to_proto_duration(params.duration)),
            selector: params.selector.clone().unwrap_or_default(),
            exporter_name: params.exporter_name.clone(),
            tags: params.tags.clone().into_iter().collect(),
            ..Default::default()
        };
        let req = client_v1::CreateLeaseRequest {
            parent: format!("namespaces/{}", self.namespace),
            lease_id: params.lease_id.clone().unwrap_or_default(),
            lease: Some(lease),
        };
        let resp = self.client.clone().create_lease(req).await?.into_inner();
        Ok(last_segment(&resp.name))
    }

    async fn get_lease(&self, name: &str) -> Result<LeaseView, ClientError> {
        // Transient-retry lives in the FSM poll loop (`lease::get_with_retry`), so
        // this is a single RPC.
        let req = client_v1::GetLeaseRequest {
            name: self.lease_path(name),
        };
        let resp = self.client.clone().get_lease(req).await?.into_inner();
        Ok(to_view(resp))
    }

    async fn delete_lease(&self, name: &str) -> Result<(), ClientError> {
        let req = client_v1::DeleteLeaseRequest {
            name: self.lease_path(name),
        };
        self.client.clone().delete_lease(req).await?;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::last_segment;

    #[test]
    fn parses_resource_identifiers() {
        assert_eq!(last_segment("namespaces/lab/leases/abc-123"), "abc-123");
        assert_eq!(last_segment("namespaces/lab/exporters/exp"), "exp");
        assert_eq!(last_segment(""), "");
        assert_eq!(last_segment("plain"), "plain");
    }
}
