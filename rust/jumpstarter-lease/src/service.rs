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
use crate::selectors::extract_match_labels_filter;

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

    fn parent(&self) -> String {
        format!("namespaces/{}", self.namespace)
    }

    fn lease_path(&self, name: &str) -> String {
        format!("namespaces/{}/leases/{}", self.namespace, name)
    }

    fn exporter_path(&self, name: &str) -> String {
        format!("namespaces/{}/exporters/{}", self.namespace, name)
    }

    /// `GetExporter` by bare name (`grpc.py:390`).
    pub async fn get_exporter(&self, name: &str) -> Result<client_v1::Exporter, ClientError> {
        let req = client_v1::GetExporterRequest {
            name: self.exporter_path(name),
        };
        Ok(self.client.clone().get_exporter(req).await?.into_inner())
    }

    /// `ListExporters` across all pages; `filter` is the full label-selector
    /// string, applied server-side (`grpc.py:190`, page_size 100).
    pub async fn list_exporters(
        &self,
        filter: Option<&str>,
    ) -> Result<Vec<client_v1::Exporter>, ClientError> {
        let mut out = Vec::new();
        let mut page_token = String::new();
        loop {
            let req = client_v1::ListExportersRequest {
                parent: self.parent(),
                page_size: 100,
                page_token: page_token.clone(),
                filter: filter.unwrap_or_default().to_string(),
            };
            let resp = self.client.clone().list_exporters(req).await?.into_inner();
            out.extend(resp.exporters);
            if resp.next_page_token.is_empty() {
                break;
            }
            page_token = resp.next_page_token;
        }
        Ok(out)
    }

    /// `ListLeases` across all pages. Only the matchLabels portion of `filter` is
    /// server-filterable (matchExpressions are enforced client-side by the caller);
    /// `only_active` excludes expired leases (`grpc.py:278`, page_size 100).
    pub async fn list_leases(
        &self,
        filter: Option<&str>,
        only_active: bool,
        tag_filter: Option<&str>,
    ) -> Result<Vec<client_v1::Lease>, ClientError> {
        let server_filter = extract_match_labels_filter(filter).unwrap_or_default();
        let mut out = Vec::new();
        let mut page_token = String::new();
        loop {
            let req = client_v1::ListLeasesRequest {
                parent: self.parent(),
                page_size: 100,
                page_token: page_token.clone(),
                filter: server_filter.clone(),
                only_active: Some(only_active),
                tag_filter: tag_filter.unwrap_or_default().to_string(),
            };
            let resp = self.client.clone().list_leases(req).await?.into_inner();
            out.extend(resp.leases);
            if resp.next_page_token.is_empty() {
                break;
            }
            page_token = resp.next_page_token;
        }
        Ok(out)
    }

    /// `UpdateLease` with a field mask derived from which fields are present.
    /// At least one of the three must be `Some` (enforced by the caller;
    /// `grpc.py:486`).
    pub async fn update_lease(
        &self,
        name: &str,
        duration: Option<Duration>,
        begin_time: Option<prost_types::Timestamp>,
        client: Option<String>,
    ) -> Result<client_v1::Lease, ClientError> {
        let mut lease = client_v1::Lease {
            name: self.lease_path(name),
            ..Default::default()
        };
        let mut paths = Vec::new();
        if let Some(d) = duration {
            lease.duration = Some(to_proto_duration(d));
            paths.push("duration".to_string());
        }
        if let Some(bt) = begin_time {
            lease.begin_time = Some(bt);
            paths.push("begin_time".to_string());
        }
        if let Some(c) = client {
            lease.client = Some(c);
            paths.push("client".to_string());
        }
        let req = client_v1::UpdateLeaseRequest {
            lease: Some(lease),
            update_mask: Some(prost_types::FieldMask { paths }),
        };
        Ok(self.client.clone().update_lease(req).await?.into_inner())
    }

    /// `RotateToken` for the current client (`grpc.py:539`).
    pub async fn rotate_token(&self) -> Result<client_v1::RotateTokenResponse, ClientError> {
        let req = client_v1::RotateTokenRequest {
            parent: self.parent(),
        };
        Ok(self.client.clone().rotate_token(req).await?.into_inner())
    }

    /// `CreateLease` returning the full created lease resource (the CLI prints it;
    /// `grpc.py:448`). The [`LeaseProvider`] impl wraps this to return just the name.
    pub async fn create_lease_raw(
        &self,
        params: &CreateLeaseParams,
    ) -> Result<client_v1::Lease, ClientError> {
        let lease = client_v1::Lease {
            duration: Some(to_proto_duration(params.duration)),
            selector: params.selector.clone().unwrap_or_default(),
            exporter_name: params.exporter_name.clone(),
            begin_time: params.begin_time,
            tags: params.tags.clone().into_iter().collect(),
            ..Default::default()
        };
        let req = client_v1::CreateLeaseRequest {
            parent: self.parent(),
            lease_id: params.lease_id.clone().unwrap_or_default(),
            lease: Some(lease),
        };
        Ok(self.client.clone().create_lease(req).await?.into_inner())
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
        Ok(last_segment(&self.create_lease_raw(params).await?.name))
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
