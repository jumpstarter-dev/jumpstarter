use pyo3::{
    prelude::*,
    types::{IntoPyDict, PyBytes, PyDict, PyList, PyTuple},
};
use pyo3_async_runtimes::TaskLocals;
use serde::{Deserialize, Serialize};
use std::{collections::HashMap, pin::Pin, sync::Arc};
use tokio::{net::UnixListener, sync::mpsc};
use tokio_stream::{
    wrappers::{ReceiverStream, UnixListenerStream},
    Stream, StreamExt,
};
use tonic::{transport::Server, Request, Response, Status, Streaming};
use uuid::Uuid;

use crate::{
    google::protobuf::{self, value::Kind},
    jumpstarter::{
        self,
        v1::{
            exporter_service_server::{ExporterService, ExporterServiceServer},
            router_service_server::{RouterService, RouterServiceServer},
            DriverCallRequest, DriverCallResponse, DriverInstanceReport, FrameType,
            GetReportResponse, LogStreamResponse, ResetRequest, ResetResponse, StreamRequest,
            StreamResponse, StreamingDriverCallRequest, StreamingDriverCallResponse,
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
            .import("jumpstarter_protocol")?
            .getattr("jumpstarter_pb2")?
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
            .import("jumpstarter_protocol")?
            .getattr("jumpstarter_pb2")?
            .getattr("DriverCallRequest")?
            .call((), Some(&args))?)
    }
}

fn convert_struct_message<'py>(
    value: &protobuf::Struct,
    message: &Bound<'py, PyAny>,
    path: String,
) -> PyResult<()> {
    message.call_method0("Clear")?;
    for (key, v) in &value.fields {
        convert_value_message(
            &v,
            &message.getattr("fields")?.get_item(&key)?,
            format!("{0}.{1}", &path, &key),
        )?;
    }
    Ok(())
}

fn convert_list_message<'py>(
    value: &protobuf::ListValue,
    message: &Bound<'py, PyAny>,
    path: String,
) -> PyResult<()> {
    message.call_method1("ClearField", ("values",))?;
    for (index, item) in value.values.iter().enumerate() {
        convert_value_message(
            item,
            &message.getattr("values")?.call_method0("add")?,
            format!("{0}[{1}]", &path, &index),
        )?;
    }
    Ok(())
}

fn convert_value_message<'py>(
    value: &protobuf::Value,
    message: &Bound<'py, PyAny>,
    path: String,
) -> PyResult<()> {
    match &value.kind {
        Some(Kind::NullValue(_)) => {
            message.setattr("null_value", 0)?;
        }
        Some(Kind::BoolValue(v)) => {
            message.setattr("bool_value", v)?;
        }
        Some(Kind::StringValue(v)) => {
            message.setattr("string_value", v)?;
        }
        Some(Kind::NumberValue(v)) => {
            message.setattr("number_value", v)?;
        }
        Some(Kind::StructValue(v)) => {
            convert_struct_message(v, &message.getattr("struct_value")?, path)?;
        }
        Some(Kind::ListValue(v)) => {
            convert_list_message(v, &message.getattr("list_value")?, path)?;
        }
        None => {}
    }
    Ok(())
}

impl<'py> FromPyObject<'py> for protobuf::Value {
    fn extract_bound(ob: &Bound<'py, PyAny>) -> PyResult<Self> {
        let kind = ob
            .call_method1("WhichOneof", ("kind",))?
            .extract::<Option<String>>()?;
        match kind.as_deref() {
            None | Some("null_value") => Ok(Self {
                kind: Some(Kind::NullValue(0)),
            }),
            Some("number_value") => Ok(Self {
                kind: Some(Kind::NumberValue(
                    ob.getattr("number_value")?.extract::<f64>()?,
                )),
            }),
            Some("bool_value") => Ok(Self {
                kind: Some(Kind::BoolValue(
                    ob.getattr("bool_value")?.extract::<bool>()?,
                )),
            }),
            Some("string_value") => Ok(Self {
                kind: Some(Kind::StringValue(
                    ob.getattr("string_value")?.extract::<String>()?,
                )),
            }),
            Some("list_value") => unimplemented!(),
            Some("struct_value") => {
                let dict = ob.getattr("struct_value")?.getattr("fields")?;
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
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        let value = py
            .import("google.protobuf.struct_pb2")?
            .getattr("Value")?
            .call0()?;
        convert_value_message(&self, &value, "".to_string())?;
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

#[derive(Clone)]
pub struct SessionExecutor {
    session: Arc<Session>,
    locals: Arc<TaskLocals>,
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
    ) -> PyResult<Self> {
        let mut mapping = HashMap::new();
        Python::with_gil(|py| {
            let uuid = if let Some(uuid) = uuid {
                Uuid::from_bytes(uuid.getattr(py, "bytes")?.extract::<[u8; 16]>(py)?)
            } else {
                Uuid::new_v4()
            };
            let devices = root_device.call_method0(py, "enumerate")?;
            let devices: &Bound<'_, PyList> = devices.downcast_bound(py)?;
            for device in devices {
                let uuid = Uuid::from_bytes(
                    device
                        .get_item(0)?
                        .getattr("bytes")?
                        .extract::<[u8; 16]>()?,
                );
                let instance = device.get_item(3)?;
                mapping.insert(uuid, instance.unbind());
            }
            Ok(Self {
                uuid,
                labels,
                root_device,
                mapping,
            })
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
    fn serve_unix_rust<'a>(&self, py: Python<'a>, path: String) -> PyResult<Bound<'a, PyAny>> {
        let locals = pyo3_async_runtimes::TaskLocals::with_running_loop(py)?.copy_context(py)?;
        let session = self.clone();
        pyo3_async_runtimes::tokio::future_into_py(py, async move {
            let uds = UnixListenerStream::new(UnixListener::bind(path).unwrap());
            let executor = SessionExecutor {
                session: Arc::new(session),
                locals: Arc::new(locals),
            };
            Server::builder()
                .add_service(
                    tonic_reflection::server::Builder::configure()
                        .register_encoded_file_descriptor_set(jumpstarter::v1::FILE_DESCRIPTOR_SET)
                        .build_v1alpha()
                        .unwrap(),
                )
                .add_service(ExporterServiceServer::new(executor.clone()))
                .add_service(RouterServiceServer::new(executor.clone()))
                .serve_with_incoming(uds)
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

#[derive(Serialize, Deserialize, Debug)]
#[serde(tag = "kind")]
#[serde(rename_all = "lowercase")]
enum StreamRequestMetadata {
    Driver { uuid: Uuid, method: String },
    Resource { uuid: Uuid },
}

impl<'py> IntoPyObject<'py> for StreamRequestMetadata {
    type Target = PyAny;
    type Output = Bound<'py, Self::Target>;
    type Error = PyErr;

    fn into_pyobject(self, py: Python<'py>) -> Result<Self::Output, Self::Error> {
        Ok(match self {
            StreamRequestMetadata::Driver { uuid, method } => {
                let args = PyDict::new(py);
                args.set_item("kind", "driver")?;
                args.set_item("uuid", uuid.to_string())?;
                args.set_item("method", method)?;
                py.import("jumpstarter.common.streams")?
                    .getattr("DriverStreamRequest")?
                    .call((), Some(&args))?
            }
            StreamRequestMetadata::Resource { uuid } => {
                let args = PyDict::new(py);
                args.set_item("uuid", uuid.to_string())?;
                args.set_item("kind", "resource")?;
                py.import("jumpstarter.common.streams")?
                    .getattr("ResourceStreamRequest")?
                    .call((), Some(&args))?
            }
        })
    }
}

#[tonic::async_trait]
impl RouterService for SessionExecutor {
    type StreamStream = StreamStream;

    async fn stream(
        &self,
        request: Request<Streaming<StreamRequest>>,
    ) -> Result<Response<Self::StreamStream>, Status> {
        let metadata: StreamRequestMetadata =
            serde_json::from_str(request.metadata().get("request").unwrap().to_str().unwrap())
                .unwrap();

        let uuid = match metadata {
            StreamRequestMetadata::Driver { uuid, .. } => uuid,
            StreamRequestMetadata::Resource { uuid, .. } => uuid,
        };

        let generator = Python::with_gil(|py| {
            let g = self
                .session
                .mapping
                .get(&uuid)
                .unwrap()
                .bind(py)
                .call_method1("Stream", (metadata, ""))
                .unwrap();

            pyo3_async_runtimes::into_future_with_locals(
                &self.locals,
                g.call_method0("__aenter__").unwrap(),
            )
        })
        .unwrap()
        .await
        .unwrap();

        let (tx, rx) = mpsc::channel(128);

        let locals = Python::with_gil(|py| self.locals.clone_ref(py));
        let generator1 = Python::with_gil(|_| generator.clone());
        let tx1 = tx.clone();
        tokio::spawn(async move {
            while let Ok(v) = Python::with_gil(|py| {
                pyo3_async_runtimes::into_future_with_locals(
                    &locals,
                    generator1.bind(py).call_method0("receive").unwrap(),
                )
            }) {
                let res = v.await;
                //dbg!("receive from python result", &res);
                if let Ok(f) = res {
                    let data = Python::with_gil(|py| {
                        f.downcast_bound::<PyBytes>(py).unwrap().as_bytes().to_vec()
                    });
                    // dbg!("received frame from python", &data);
                    tx1.send(Ok(StreamResponse {
                        payload: data,
                        frame_type: FrameType::Data.into(),
                    }))
                    .await
                    .unwrap();
                } else {
                    tx1.send(Ok(StreamResponse {
                        payload: vec![],
                        frame_type: FrameType::Goaway.into(),
                    }))
                    .await;
                    println!("done receiving from python");
                    break;
                }
            }
        });

        let locals = Python::with_gil(|py| self.locals.clone_ref(py));
        let generator2 = Python::with_gil(|_| generator.clone());
        let tx2 = tx.clone();
        tokio::spawn(async move {
            let mut request = request.into_inner();
            while let Some(frame) = request.next().await {
                // dbg!("sending frame to python", &frame);
                let frame = frame.unwrap();
                match frame.frame_type() {
                    FrameType::Data => {
                        if let Ok(v) = Python::with_gil(|py| {
                            let payload = PyBytes::new(py, &frame.payload);
                            pyo3_async_runtimes::into_future_with_locals(
                                &locals,
                                generator2
                                    .bind(py)
                                    .call_method1("send", (payload,))
                                    .unwrap(),
                            )
                        }) {
                            let res = v.await;
                            // dbg!("send to python result", &res);
                            if res.is_err() {
                                break;
                            }
                        }
                    }
                    _ => {
                        break;
                    }
                }
            }
            println!("done with sending to python");
            // Python::with_gil(|py| {
            //     pyo3_async_runtimes::into_future_with_locals(
            //         &locals,
            //         generator2
            //             .bind(py)
            //             .call_method1(
            //                 "send",
            //                 (StreamRequest {
            //                     payload: vec![],
            //                     frame_type: FrameType::Goaway.into(),
            //                 },),
            //             )
            //             .unwrap(),
            //     )
            // })
            // .unwrap()
            // .await
            // .unwrap();
            drop(tx2);
        });

        Ok(Response::new(Box::pin(ReceiverStream::new(rx))))
    }
}