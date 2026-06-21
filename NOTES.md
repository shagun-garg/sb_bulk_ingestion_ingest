# Project Next Steps & Architecture Evolution

## 1. Given Two More Days :-

If granted an additional 48 hours, the focus would shift from "making it work" to "making it production-ready" by addressing the following areas:

* **Layered Architecture (Controller-Service-DAO):** Strictly decouple transport, business logic, and data access layers. This ensures the codebase remains maintainable, testable, and loosely coupled.
* **Structured Logging & Observability:** Implement contextual, structured logging (e.g., JSON format with correlation IDs) instead of basic print statements. This is crucial for tracking requests across asynchronous boundaries.
* **Refactoring Async Abuses:** Ensure asynchronous operations are handled predictably. If a process expects synchronous deterministic behavior, remove unnecessary asynchronous overhead to prevent race conditions and unhandled promise/thread rejections.


## 2. What would you change if this service had to handle 100,000 records per request?

Processing 100k records in a single synchronous block will CPU and memory, likely causing timeouts.

* **Parallel Worker Pools:** As you noted, breaking the payload into smaller chunks (e.g., chunks of 5,000 - 10,000) and processing them via a worker thread pool (or Goroutines/ExecutorServices depending on the stack) is essential to utilize multi-core architecture.
* **Memory Management & Streaming:** Instead of loading all 100k records into application memory at once, implement request streaming or memory-mapped file processing to keep the memory footprint low and stable.

## 3. Architecture for Long-Running Jobs (Asynchronous Processing)

## COMPARISION: 

### AWS Lambda

* Maximum execution time: 15 minutes.

* Operational complexity: Low (serverless, no cluster management).

* Cost structure: Pay-per-use based on execution time.

* Best suited for: Short-lived processing tasks, event-driven workflows, and lightweight integrations.

### AWS ECS (Fargate)

* Maximum execution time: Indefinite / long-running workloads.

* Operational complexity: Medium (requires containers, task definitions, and scaling configuration).

* Cost structure: Pay for provisioned CPU and memory while tasks are running.

* Best suited for: Heavy compute jobs and workloads that exceed Lambda's 15-minute limit.

### AWS Step Functions

* Maximum execution time: Up to 1 year.

* Operational complexity: Low to medium (workflow orchestration through state-machine definitions).

* Cost structure: Pay per state transition.

* Best suited for: Orchestrating multiple services, retries, approvals, and error-handling workflows.


### Architectural Recommendation:

Use Step Functions + Lambda. The Step Function can split the 10k records, trigger Lambda functions in parallel to process chunks.


## 4. Testing the `/cancel` vs `/ingest` Race Condition

Testing a race condition where `/cancel` hits mid-way through a `/ingest` write to Zoho requires validating that data writing halts immediately and no orphaned/corrupted states remain.

### The Testing Strategy

#### A. Unit / Integration Level (Mocked Network)

1. **Inject a Controlled Delay:** In the test environment, mock the Zoho API client to introduce an intentional 2-to-3 second latency for every batch write.
2. **Trigger Concurrent Requests:** * Fire the `/ingest` request.
* Sleep for 500ms (ensuring it is actively "writing" to the mocked Zoho).
* Fire the `/cancel` request with the same transaction/job ID.


3. **Assertions:** * Verify that `/cancel` returns a `200 OK` or `202 Accepted`.
* Verify that the `/ingest` process aborts and throws/returns a cancelled exception.
* Verify that the total number of mock calls to Zoho stops increasing after the cancellation timestamp.

