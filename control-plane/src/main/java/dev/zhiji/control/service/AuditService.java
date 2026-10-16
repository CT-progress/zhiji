package dev.zhiji.control.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.zhiji.control.model.AuditLog;
import dev.zhiji.control.model.OutboxEvent;
import dev.zhiji.control.model.OutboxStatus;
import dev.zhiji.control.repository.AuditLogRepository;
import dev.zhiji.control.repository.OutboxEventRepository;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Instant;
import java.util.Map;

/**
 * Records audit events and corresponding outbox entries in a single transaction.
 *
 * <p>Each call to {@code record()} writes an immutable {@link AuditLog} row
 * plus a {@link OutboxEvent} row (status PENDING). The outbox publisher later
 * publishes the event to RabbitMQ and marks it SENT.
 */
@Service
public class AuditService {

    private static final Logger log = LoggerFactory.getLogger(AuditService.class);

    private final AuditLogRepository auditLogRepository;
    private final OutboxEventRepository outboxEventRepository;
    private final ObjectMapper objectMapper;

    public AuditService(AuditLogRepository auditLogRepository,
                        OutboxEventRepository outboxEventRepository,
                        ObjectMapper objectMapper) {
        this.auditLogRepository = auditLogRepository;
        this.outboxEventRepository = outboxEventRepository;
        this.objectMapper = objectMapper;
    }

    @Transactional
    public AuditLog record(String userId, String action, String resourceType,
                           String resourceId, String traceId, Map<String, Object> details) {
        Instant now = Instant.now();
        String detailsJson = serializeDetails(details);

        AuditLog auditLog = new AuditLog(now, userId, action, resourceType,
            resourceId, traceId, detailsJson);
        auditLogRepository.save(auditLog);

        OutboxEvent outboxEvent = new OutboxEvent(
            now, resourceType, resourceId, action, detailsJson, OutboxStatus.PENDING);
        outboxEventRepository.save(outboxEvent);

        log.debug("audit: user={} action={} resource={}/{} trace={}",
            userId, action, resourceType, resourceId, traceId);
        return auditLog;
    }

    private String serializeDetails(Map<String, Object> details) {
        if (details == null || details.isEmpty()) {
            return "{}";
        }
        try {
            return objectMapper.writeValueAsString(details);
        } catch (Exception e) {
            log.warn("failed to serialize audit details: {}", e.getMessage());
            return "{}";
        }
    }
}
