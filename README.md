This repository includes two container images that together serve as an adapter that on the one hand, exposes the API of [Testing Farm (TF)](https://docs.testing-farm.io/Testing%20Farm/0.1/index.html), and on the other hand, runs a pipeline on OpenShift. This can be used as a temporary solution for migrating from TF to OpenShift pipelines / Konflux.  

The first component `tf-api` handles TF requests and requests for board inventories. Its container image is stored in https://quay.io/repository/ahadas/tf_api.  

The second component `artifacts` handles requests for both http and rsync requests for getting test artifacts. Its container image is stored in https://quay.io/repository/ahadas/tf_artifacts.  

The `conf` folder holds two deployments of the above mentioned containers, and a deployment of test-console (TC) which is a client that used to interact with TF.  

As an example of an OpenShift/Tekton pipeline that can be triggered, the `pipeline` folder contains the definition of a pipeline that runs tests uusing Testing Farm's `tmt` command. You can deploy is with:
```bash
oc -n <namespace> apply -f pipeline/.tekton/
```

oc -n demo create secret generic kubeconfig-secret --from-file=kubeconfig=conf/kubeconfig.yaml
