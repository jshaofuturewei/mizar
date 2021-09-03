Please follow the following steps to add gateway to a subnet
1. kubectl apply -f docs/gateway/vpc.yaml
2. kubectl apply -f docs/gateway/subnet.yaml
3. kubectl apply -f docs/gateway/pod.yaml
4. Update the gateway.yaml file in docs/gateway
5. kubectl patch subnet net0 --patch "$(cat ../gateway.yaml)" --type=merge
