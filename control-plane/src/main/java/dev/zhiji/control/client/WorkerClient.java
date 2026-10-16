package dev.zhiji.control.client;

import dev.zhiji.control.config.WorkerProperties;
import dev.zhiji.control.model.*;
import io.github.resilience4j.circuitbreaker.annotation.CircuitBreaker;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Component;
import org.springframework.web.client.RestClient;

/**
 * HTTP client for talking to the Python worker.
 *
 * <p>Implements the Worker API side of the contract:
 * dispatch tasks, cancel tasks, check health.
 *
 * <p>Circuit breakers protect against cascading failures when the worker
 * is unavailable. {@code worker-dispatch} guards dispatch/cancel calls;
 * {@code worker-health} guards health checks. Exceptions propagate to
 * the controller, which translates them to appropriate HTTP status codes.
 */
@Component
public class WorkerClient {

    private static final Logger log = LoggerFactory.getLogger(WorkerClient.class);

    private final RestClient restClient;
    private final WorkerProperties props;

    public WorkerClient(RestClient restClient, WorkerProperties props) {
        this.restClient = restClient;
        this.props = props;
    }

    /**
     * Dispatch a task to the worker.
     *
     * @return the worker's dispatch response
     * @throws Exception if the worker is unreachable (circuit breaker tracks failures)
     */
    @CircuitBreaker(name = "worker-dispatch")
    public DispatchResponse dispatch(TaskDispatch dispatch) {
        return restClient.post()
            .uri("/internal/v1/tasks:dispatch")
            .header("X-Trace-Id", dispatch.traceId())
            .body(dispatch)
            .retrieve()
            .body(DispatchResponse.class);
    }

    /**
     * Cancel a running task on the worker.
     *
     * @throws Exception if the worker is unreachable
     */
    @CircuitBreaker(name = "worker-dispatch")
    public CancelResponse cancel(String taskId, String traceId) {
        return restClient.post()
            .uri("/internal/v1/tasks/{taskId}:cancel", taskId)
            .header("X-Trace-Id", traceId)
            .retrieve()
            .body(CancelResponse.class);
    }

    /**
     * Check worker health.
     *
     * <p>Catches exceptions internally and returns a degraded status,
     * so the circuit breaker does not track failures for this method.
     * The annotation is present for future use when a dedicated health
     * controller is added.
     */
    @CircuitBreaker(name = "worker-health")
    public WorkerHealth health() {
        try {
            return restClient.get()
                .uri("/internal/v1/worker/health")
                .retrieve()
                .body(WorkerHealth.class);
        } catch (Exception e) {
            log.error("worker health check failed: {}", e.getMessage());
            return new WorkerHealth("degraded", "unknown", java.util.List.of());
        }
    }
}
