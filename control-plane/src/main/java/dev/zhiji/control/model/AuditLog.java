package dev.zhiji.control.model;

import jakarta.persistence.*;
import java.time.Instant;

/**
 * Append-only audit log entry.
 *
 * <p>Every credential use and task lifecycle event is recorded here.
 * No update or delete operations are exposed — the log is immutable.
 */
@Entity
@Table(name = "audit_log")
public class AuditLog {

    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;

    @Column(name = "event_time", nullable = false, updatable = false)
    private Instant eventTime;

    @Column(name = "user_id", length = 36, updatable = false)
    private String userId;

    @Column(name = "action", nullable = false, updatable = false, length = 64)
    private String action;

    @Column(name = "resource_type", nullable = false, updatable = false, length = 16)
    private String resourceType;

    @Column(name = "resource_id", updatable = false, length = 64)
    private String resourceId;

    @Column(name = "trace_id", updatable = false, length = 64)
    private String traceId;

    @Column(name = "details", updatable = false, length = 4096)
    private String details;

    protected AuditLog() {}

    public AuditLog(Instant eventTime, String userId, String action,
                    String resourceType, String resourceId,
                    String traceId, String details) {
        this.eventTime = eventTime;
        this.userId = userId;
        this.action = action;
        this.resourceType = resourceType;
        this.resourceId = resourceId;
        this.traceId = traceId;
        this.details = details;
    }

    public Long getId() { return id; }
    public Instant getEventTime() { return eventTime; }
    public String getUserId() { return userId; }
    public String getAction() { return action; }
    public String getResourceType() { return resourceType; }
    public String getResourceId() { return resourceId; }
    public String getTraceId() { return traceId; }
    public String getDetails() { return details; }
}
