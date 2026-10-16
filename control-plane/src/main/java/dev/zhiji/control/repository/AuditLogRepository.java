package dev.zhiji.control.repository;

import dev.zhiji.control.model.AuditLog;
import org.springframework.data.jpa.repository.JpaRepository;
import org.springframework.stereotype.Repository;

import java.util.List;

/**
 * Read-only repository for the append-only audit log.
 *
 * <p>No update or delete methods are added — the log is immutable.
 */
@Repository
public interface AuditLogRepository extends JpaRepository<AuditLog, Long> {

    List<AuditLog> findByUserIdOrderByEventTimeDesc(String userId);

    List<AuditLog> findByResourceIdOrderByEventTimeDesc(String resourceId);

    List<AuditLog> findByTraceIdOrderByEventTimeDesc(String traceId);
}
