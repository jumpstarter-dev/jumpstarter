use pyo3::{
    prelude::*,
    types::{IntoPyDict, PyDict, PyList, PyTuple},
};
use pyo3_async_runtimes::TaskLocals;
use std::{collections::HashMap, pin::Pin};
use tokio::{net::UnixListener, sync::mpsc};
use tokio_stream::{
    wrappers::{ReceiverStream, UnixListenerStream},
    Stream,
};
use tonic::{transport::Server, Request, Response, Status, Streaming};
use uuid::Uuid;

use crate::{
    google::protobuf::{self, value::Kind},
    jumpstarter::{
        self,
        v1::{
            exporter_service_server::{ExporterService, ExporterServiceServer},
            router_service_server::RouterService,
            DriverCallRequest, DriverCallResponse, DriverInstanceReport, GetReportResponse,
            LogStreamResponse, ResetRequest, ResetResponse, StreamRequest, StreamResponse,
            StreamingDriverCallRequest, StreamingDriverCallResponse,
        },
    },
};

impl<'py> IntoPyObject<'py> for DriverCallRequest {
    type Target = PyAny;
    type Output = Bound<'py, Self::Target>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        let args = PyDict::new(py);
        args.set_item("uuid", self.uuid)?;
        args.set_item("method", self.method)?;
        args.set_item("args", self.args)?;
        Ok(py
            .import("jumpstarter.v1.jumpstarter_pb2")?
            .getattr("DriverCallRequest")?
            .call((), Some(&args))?)
    }
}

impl<'py> IntoPyObject<'py> for StreamingDriverCallRequest {
    type Target = PyAny;
    type Output = Bound<'py, Self::Target>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        let args = PyDict::new(py);
        args.set_item("uuid", self.uuid)?;
        args.set_item("method", self.method)?;
        args.set_item("args", self.args)?;
        Ok(py
            .import("jumpstarter.v1.jumpstarter_pb2")?
            .getattr("DriverCallRequest")?
            .call((), Some(&args))?)
    }
}

fn convert_struct_message<'py>(
    value: &protobuf::Struct,
    message: &Bound<'py, PyAny>,
    path: String,
) {
    message.call_method0("Clear").unwrap();
    for (key, v) in &value.fields {
        convert_value_message(
            &v,
            &message.getattr("fields").unwrap().get_item(&key).unwrap(),
            format!("{0}.{1}", &path, &key),
        );
    }
}

fn convert_list_message<'py>(
    value: &protobuf::ListValue,
    message: &Bound<'py, PyAny>,
    path: String,
) {
    message.call_method1("ClearField", ("values",)).unwrap();
    for (index, item) in value.values.iter().enumerate() {
        convert_value_message(
            item,
            &message
                .getattr("values")
                .unwrap()
                .call_method0("add")
                .unwrap(),
            format!("{0}[{1}]", &path, &index),
        );
    }
}

fn convert_value_message<'py>(value: &protobuf::Value, message: &Bound<'py, PyAny>, path: String) {
    match &value.kind {
        Some(Kind::NullValue(_)) => {
            message.setattr("null_value", 0).unwrap();
        }
        Some(Kind::BoolValue(v)) => {
            message.setattr("bool_value", v).unwrap();
        }
        Some(Kind::StringValue(v)) => {
            message.setattr("string_value", v).unwrap();
        }
        Some(Kind::NumberValue(v)) => {
            message.setattr("number_value", v).unwrap();
        }
        Some(Kind::StructValue(v)) => {
            convert_struct_message(v, &message.getattr("struct_value").unwrap(), path);
        }
        Some(Kind::ListValue(v)) => {
            convert_list_message(v, &message.getattr("list_value").unwrap(), path);
        }
        None => {}
    }
}

impl<'py> FromPyObject<'py> for protobuf::Value {
    fn extract_bound(ob: &Bound<'py, PyAny>) -> PyResult<Self> {
        let kind = ob
            .call_method1("WhichOneof", ("kind",))
            .unwrap()
            .extract::<Option<String>>()
            .unwrap();
        match kind.as_deref() {
            None | Some("null_value") => Ok(Self {
                kind: Some(Kind::NullValue(0)),
            }),
            Some("number_value") => Ok(Self {
                kind: Some(Kind::NumberValue(
                    ob.getattr("number_value")
                        .unwrap()
                        .extract::<f64>()
                        .unwrap(),
                )),
            }),
            Some("bool_value") => Ok(Self {
                kind: Some(Kind::BoolValue(
                    ob.getattr("bool_value").unwrap().extract::<bool>().unwrap(),
                )),
            }),
            Some("string_value") => Ok(Self {
                kind: Some(Kind::StringValue(
                    ob.getattr("string_value")
                        .unwrap()
                        .extract::<String>()
                        .unwrap(),
                )),
            }),
            Some("list_value") => unimplemented!(),
            Some("struct_value") => {
                let dict = ob
                    .getattr("struct_value")
                    .unwrap()
                    .getattr("fields")
                    .unwrap();
                Ok(Self {
                    kind: Some(Kind::StructValue(protobuf::Struct {
                        fields: dict
                            .try_iter()
                            .unwrap()
                            .map(|l| {
                                let key = l.unwrap().extract::<String>().unwrap();
                                let value = dict
                                    .get_item(&key)
                                    .unwrap()
                                    .extract::<protobuf::Value>()
                                    .unwrap();
                                (key, value)
                            })
                            .collect::<HashMap<String, protobuf::Value>>(),
                    })),
                })
            }
            Some(_) => unimplemented!(),
        }
    }
}

impl<'py> IntoPyObject<'py> for protobuf::Value {
    type Target = PyAny;
    type Output = Bound<'py, Self::Target>;
    type Error = std::convert::Infallible;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        let value = py
            .import("google.protobuf.struct_pb2")
            .unwrap()
            .getattr("Value")
            .unwrap()
            .call0()
            .unwrap();
        convert_value_message(&self, &value, "".to_string());
        Ok(value)
    }
}

#[pyclass(subclass)]
#[derive(Clone)]
pub struct Session {
    pub uuid: Uuid,
    pub labels: HashMap<String, String>,
    pub root_device: Py<PyAny>,
    mapping: HashMap<Uuid, Py<PyAny>>,
}

pub struct SessionExecutor {
    session: Session,
    locals: TaskLocals,
}

type StreamingDriverCallStream =
    Pin<Box<dyn Stream<Item = Result<StreamingDriverCallResponse, Status>> + Send>>;
type LogStreamStream = Pin<Box<dyn Stream<Item = Result<LogStreamResponse, Status>> + Send>>;
type StreamStream = Pin<Box<dyn Stream<Item = Result<StreamResponse, Status>> + Send>>;

#[pymethods]
impl Session {
    #[new]
    #[pyo3(signature = (*, uuid = None, labels = Default::default(), root_device))]
    fn new(
        uuid: Option<Py<PyAny>>,
        labels: HashMap<String, String>,
        root_device: Py<PyAny>,
    ) -> Self {
        let mut mapping = HashMap::new();
        Python::with_gil(|py| {
            let uuid = if let Some(uuid) = uuid {
                Uuid::from_bytes(
                    uuid.getattr(py, "bytes")
                        .unwrap()
                        .extract::<[u8; 16]>(py)
                        .unwrap(),
                )
            } else {
                Uuid::new_v4()
            };
            let devices = root_device.call_method0(py, "enumerate").unwrap();
            let devices: &Bound<'_, PyList> = devices.downcast_bound(py).unwrap();
            for device in devices {
                let uuid = Uuid::from_bytes(
                    device
                        .get_item(0)
                        .unwrap()
                        .getattr("bytes")
                        .unwrap()
                        .extract::<[u8; 16]>()
                        .unwrap(),
                );
                let instance = device.get_item(3).unwrap();
                dbg!(&uuid, device.get_item(2).unwrap());
                mapping.insert(uuid, instance.unbind());
            }
            Self {
                uuid,
                labels,
                root_device,
                mapping,
            }
        })
    }
    fn __enter__(slf: Py<Self>, py: Python) -> PyResult<Py<Self>> {
        slf.borrow(py).root_device.call_method0(py, "reset")?;
        Ok(slf)
    }
    fn __exit__(
        &self,
        py: Python,
        _exc_type: &crate::Bound<'_, crate::PyAny>,
        _exc_value: &crate::Bound<'_, crate::PyAny>,
        _traceback: &crate::Bound<'_, crate::PyAny>,
    ) {
        self.root_device.call_method0(py, "close").unwrap();
    }
    fn serve_unix<'a>(&self, py: Python<'a>, path: String) -> PyResult<Bound<'a, PyAny>> {
        let locals = pyo3_async_runtimes::TaskLocals::with_running_loop(py)?.copy_context(py)?;
        let session = self.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let uds = UnixListenerStream::new(UnixListener::bind(path).unwrap());
            Server::builder()
                .add_service(
                    tonic_reflection::server::Builder::configure()
                        .register_encoded_file_descriptor_set(jumpstarter::v1::FILE_DESCRIPTOR_SET)
                        .build_v1alpha()
                        .unwrap(),
                )
                .add_service(ExporterServiceServer::new(SessionExecutor {
                    session,
                    locals,
                }))
                .serve_with_incoming(uds)
                .await
                .unwrap();
            Ok(())
        })
    }
    fn serve_tcp<'a>(&self, py: Python<'a>) -> PyResult<Bound<'a, PyAny>> {
        let locals = pyo3_async_runtimes::TaskLocals::with_running_loop(py)?.copy_context(py)?;
        let session = self.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let addr = "127.0.0.1:50051".parse().unwrap();
            Server::builder()
                .add_service(
                    tonic_reflection::server::Builder::configure()
                        .register_encoded_file_descriptor_set(jumpstarter::v1::FILE_DESCRIPTOR_SET)
                        .build_v1alpha()
                        .unwrap(),
                )
                .add_service(ExporterServiceServer::new(SessionExecutor {
                    session,
                    locals,
                }))
                .serve(addr)
                .await
                .unwrap();
            Ok(())
        })
    }
}

#[tonic::async_trait]
impl ExporterService for SessionExecutor {
    type StreamingDriverCallStream = StreamingDriverCallStream;
    type LogStreamStream = LogStreamStream;
    async fn get_report(
        &self,
        _request: Request<protobuf::Empty>,
    ) -> Result<Response<GetReportResponse>, Status> {
        let mut reports = vec![];
        Python::with_gil(|py| {
            let devices = self
                .session
                .root_device
                .call_method0(py, "enumerate")
                .unwrap();
            let devices: &Bound<'_, PyList> = devices.downcast_bound(py).unwrap();
            for device in devices {
                let t: &Bound<'_, PyTuple> = device.downcast().unwrap();
                let parent = t.get_item(1).unwrap();
                let name = t.get_item(2).unwrap();
                let instance = t.get_item(3).unwrap();
                let report = instance
                    .call_method(
                        "report",
                        (),
                        Some(
                            &[("parent", parent), ("name", name)]
                                .into_py_dict(py)
                                .unwrap(),
                        ),
                    )
                    .unwrap();
                let uuid = report.getattr("uuid").unwrap().extract::<String>().unwrap();
                let parent_uuid = report
                    .getattr("parent_uuid")
                    .unwrap()
                    .extract::<Option<String>>()
                    .unwrap();
                let labels = report.getattr("labels").unwrap();
                let labels = labels
                    .try_iter()
                    .unwrap()
                    .map(|l| {
                        let key = l.unwrap().extract::<String>().unwrap();
                        let value = labels.get_item(&key).unwrap().extract::<String>().unwrap();
                        (key, value)
                    })
                    .collect::<HashMap<String, String>>();
                reports.push(DriverInstanceReport {
                    uuid,
                    parent_uuid,
                    labels,
                })
            }
        });
        Ok(Response::new(GetReportResponse {
            uuid: self.session.uuid.to_string(),
            labels: self.session.labels.clone(),
            reports,
        }))
    }
    async fn driver_call(
        &self,
        request: Request<DriverCallRequest>,
    ) -> Result<Response<DriverCallResponse>, Status> {
        let request = request.into_inner();
        let uuid = Uuid::parse_str(&request.uuid).unwrap();
        let fut = Python::with_gil(|py| {
            pyo3_async_runtimes::into_future_with_locals(
                &self.locals,
                self.session
                    .mapping
                    .get(&uuid)
                    .unwrap()
                    .bind(py)
                    .call_method1("DriverCall", (request, ""))
                    .unwrap(),
            )
            .unwrap()
        });

        let res = fut.await.unwrap();

        let res = Python::with_gil(|py| res.extract::<DriverCallResponse>(py)).unwrap();

        Ok(Response::new(res))
    }
    async fn streaming_driver_call(
        &self,
        request: Request<StreamingDriverCallRequest>,
    ) -> Result<Response<Self::StreamingDriverCallStream>, Status> {
        let request = request.into_inner();
        let uuid = Uuid::parse_str(&request.uuid).unwrap();

        let (tx, rx) = mpsc::channel(128);

        let generator = Python::with_gil(|py| {
            self.session
                .mapping
                .get(&uuid)
                .unwrap()
                .bind(py)
                .call_method1("StreamingDriverCall", (request, ""))
                .unwrap()
                .unbind()
        });

        dbg!(&generator);

        let locals = Python::with_gil(|py| self.locals.clone_ref(py));

        tokio::spawn(async move {
            while let Ok(v) = Python::with_gil(|py| {
                pyo3_async_runtimes::into_future_with_locals(
                    &locals,
                    generator.bind(py).call_method0("__anext__").unwrap(),
                )
            }) {
                if let Ok(v) = v.await {
                    tx.send(Python::with_gil(|py| {
                        Ok(v.extract::<StreamingDriverCallResponse>(py).unwrap())
                    }))
                    .await
                    .unwrap();
                } else {
                    break;
                }
            }
        });

        Ok(Response::new(Box::pin(ReceiverStream::new(rx))))
    }
    async fn log_stream(
        &self,
        request: Request<protobuf::Empty>,
    ) -> Result<Response<Self::LogStreamStream>, Status> {
        unimplemented!()
    }
    async fn reset(
        &self,
        _request: Request<ResetRequest>,
    ) -> Result<Response<ResetResponse>, Status> {
        Ok(Response::new(ResetResponse {}))
    }
}

#[tonic::async_trait]
impl RouterService for SessionExecutor {
    type StreamStream = StreamStream;

    async fn stream(
        &self,
        request: Request<Streaming<StreamRequest>>,
    ) -> Result<Response<Self::StreamStream>, Status> {
        todo!()
    }
}
