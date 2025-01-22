Build: podman build . -t server  
Run: podman run -p 8080:8080 localhost/server  

In order to run it on OpenShift:  
oc -n <namespace> apply -f ocp/deployment.yaml  
oc -n <namespace> expose deployment/tf-adapter  
oc -n <namespace> expose service/tf-adapter  
