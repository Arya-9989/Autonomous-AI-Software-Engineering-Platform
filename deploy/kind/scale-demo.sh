#!/usr/bin/env bash
# Prove KEDA scales the worker Deployment on queue depth, and that no job is lost while it
# scales back down. Creates a kind cluster, installs KEDA, deploys deploy/kind, submits jobs,
# samples the worker replica count, checks every job succeeded, and deletes the cluster.
#
#   docker build -t autoswe:ci .        # the image the overlay uses
#   bash deploy/kind/scale-demo.sh      # needs docker, kind, kubectl, helm, curl, python3
#
# Overridable: KIND, HELM, KUBECTL (binaries), CLUSTER, PORT, JOBS, KEDA_VERSION.
set -euo pipefail

KIND=${KIND:-kind}
HELM=${HELM:-helm}
KUBECTL=${KUBECTL:-kubectl}
CLUSTER=${CLUSTER:-autoswe-scale-demo}
PORT=${PORT:-5367}
JOBS=${JOBS:-10}
KEDA_VERSION=${KEDA_VERSION:-2.21.0}
cd "$(dirname "$0")"  # relative paths below: they work for every kubectl build
CTX="kind-$CLUSTER"
k() { "$KUBECTL" --context "$CTX" "$@"; }
log() { printf '%s %s\n' "$(date +%H:%M:%S)" "$*"; }

cleanup() {
  [[ -n "${FORWARD_PID:-}" ]] && kill "$FORWARD_PID" 2>/dev/null || true
  [[ "${KEEP_CLUSTER:-}" == "1" ]] || "$KIND" delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "creating kind cluster $CLUSTER"
"$KIND" create cluster --name "$CLUSTER" --wait 120s >/dev/null
"$KIND" load docker-image autoswe:ci --name "$CLUSTER" >/dev/null

log "installing KEDA $KEDA_VERSION"
"$HELM" repo add kedacore https://kedacore.github.io/charts >/dev/null 2>&1 || true
"$HELM" upgrade --install keda kedacore/keda --version "$KEDA_VERSION" --kube-context "$CTX" \
  --namespace keda --create-namespace --wait --timeout 5m >/dev/null

log "deploying PostgreSQL and AutoSWE"
k apply -f postgres.yaml >/dev/null
k -n autoswe-db rollout status deploy/postgres --timeout 180s >/dev/null
k apply -k . >/dev/null
k -n autoswe rollout status deploy/autoswe --timeout 240s >/dev/null
k -n autoswe rollout status deploy/autoswe-worker --timeout 240s >/dev/null
for _ in $(seq 1 60); do
  [[ "$(k -n autoswe get scaledobject autoswe-worker -o jsonpath='{.status.conditions[?(@.type=="Ready")].status}')" == "True" ]] && break
  sleep 2
done
log "ScaledObject ready: $(k -n autoswe get scaledobject autoswe-worker --no-headers)"

k -n autoswe port-forward svc/autoswe "$PORT:80" >/dev/null 2>&1 &
FORWARD_PID=$!
for _ in $(seq 1 30); do curl -sf "localhost:$PORT/readyz" >/dev/null && break; sleep 1; done
AUTH="X-API-Key: kind-demo-api-key-0123456789"

replicas() { k -n autoswe get deploy autoswe-worker -o jsonpath='{.status.replicas}'; }
log "workers before: $(replicas)"
IDS=()
for i in $(seq 1 "$JOBS"); do
  IDS+=("$(curl -sf -X POST "localhost:$PORT/api/v1/jobs" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"spec\": \"Inventory service number $i. Entities: Product(name, price:float, stock:int)\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')")
done
log "submitted $JOBS jobs"

status_counts() {
  for id in "${IDS[@]}"; do
    curl -sf "localhost:$PORT/api/v1/jobs/$id" -H "$AUTH" | python3 -c 'import json,sys; print(json.load(sys.stdin)["status"])'
  done | sort | uniq -c | tr '\n' ' '
}
PEAK=0
for _ in $(seq 1 90); do
  now=$(replicas); (( now > PEAK )) && PEAK=$now
  counts=$(status_counts)
  log "workers=$now  jobs: $counts"
  [[ "$counts" =~ ^[[:space:]]*$JOBS\ succeeded ]] && break
  sleep 5
done
log "peak workers: $PEAK"

log "waiting for scale-down (no lost jobs while workers go away)"
for _ in $(seq 1 40); do
  now=$(replicas); log "workers=$now"; [[ "$now" == "1" ]] && break; sleep 5
done
FINAL=$(status_counts)
log "final job states: $FINAL"
[[ "$FINAL" =~ ^[[:space:]]*$JOBS\ succeeded ]] || { echo "FAIL: not every job succeeded"; exit 1; }
(( PEAK > 1 )) || { echo "FAIL: KEDA did not scale the workers up"; exit 1; }
DOWN=$(replicas)

log "graceful shutdown: restart every worker while jobs run"
IDS=()
for i in 1 2 3 4; do
  IDS+=("$(curl -sf -X POST "localhost:$PORT/api/v1/jobs" -H "$AUTH" -H 'Content-Type: application/json' \
    -d "{\"spec\": \"Expenses tracker number $i with description, amount:float and spent_on:date.\"}" \
    | python3 -c 'import json,sys; print(json.load(sys.stdin)["id"])')")
done
for _ in $(seq 1 30); do [[ "$(status_counts)" == *running* ]] && break; sleep 1; done
log "before restart: $(status_counts)"
k -n autoswe rollout restart deploy/autoswe-worker >/dev/null
for _ in $(seq 1 60); do
  counts=$(status_counts); log "jobs: $counts"
  [[ "$counts" =~ ^[[:space:]]*4\ succeeded ]] && break
  sleep 5
done
REQUEUED=0
for id in "${IDS[@]}"; do
  curl -sf "localhost:$PORT/api/v1/jobs/$id/events" -H "$AUTH" | grep -q 'job.requeued' && REQUEUED=$((REQUEUED + 1))
done
[[ "$(status_counts)" =~ ^[[:space:]]*4\ succeeded ]] || { echo "FAIL: a job was lost in the restart"; exit 1; }
log "all 4 jobs succeeded across the restart ($REQUEUED requeued after the drain, the rest drained)"
log "OK: scaled 1 -> $PEAK -> $DOWN; all $JOBS jobs succeeded; no job lost in a rolling restart"
