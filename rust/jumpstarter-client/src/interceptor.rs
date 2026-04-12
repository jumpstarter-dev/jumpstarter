//! UUID metadata interceptor for driver instance routing.

use tonic::service::Interceptor;
use tonic::{Request, Status};

const UUID_HEADER: &str = "x-jumpstarter-driver-uuid";

/// gRPC interceptor that injects the `x-jumpstarter-driver-uuid` metadata
/// header into every outgoing call, routing it to the correct driver instance.
#[derive(Debug, Clone)]
pub struct UuidInterceptor {
    uuid: String,
}

impl UuidInterceptor {
    /// Create a new interceptor for the given driver instance UUID.
    pub fn new(uuid: impl Into<String>) -> Self {
        Self { uuid: uuid.into() }
    }
}

impl Interceptor for UuidInterceptor {
    fn call(&mut self, mut request: Request<()>) -> Result<Request<()>, Status> {
        request.metadata_mut().insert(
            UUID_HEADER,
            self.uuid
                .parse()
                .map_err(|_| Status::internal("invalid UUID metadata value"))?,
        );
        Ok(request)
    }
}
