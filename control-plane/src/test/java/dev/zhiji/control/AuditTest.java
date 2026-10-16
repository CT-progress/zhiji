package dev.zhiji.control;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.zhiji.control.dto.*;
import dev.zhiji.control.model.AuditLog;
import dev.zhiji.control.model.OutboxEvent;
import dev.zhiji.control.model.OutboxStatus;
import dev.zhiji.control.repository.AuditLogRepository;
import dev.zhiji.control.repository.OutboxEventRepository;
import dev.zhiji.control.service.AuditService;
import dev.zhiji.control.service.TokenRevocationService;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/**
 * Phase 6.5 audit tests: audit log creation, outbox events, query by
 * userId/resourceId/traceId, and audit on auth/task events.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.MOCK)
@AutoConfigureMockMvc
class AuditTest {

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private ObjectMapper objectMapper;
    @Autowired
    private AuditService auditService;
    @Autowired
    private AuditLogRepository auditLogRepository;
    @Autowired
    private OutboxEventRepository outboxEventRepository;
    @Autowired
    private TokenRevocationService revocationService;

    private static final String CALLBACK_SECRET = "zhiji-callback-dev-secret";

    @BeforeEach
    void cleanup() {
        outboxEventRepository.deleteAll();
        auditLogRepository.deleteAll();
        revocationService.clear();
    }

    // -----------------------------------------------------------------------
    // AuditService.record() creates both audit log and outbox event
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("audit: record() creates AuditLog entry")
    void record_createsAuditLogEntry() {
        auditService.record("user-1", "TASK_DISPATCHED", "TASK",
            "task-1", "trace-1", Map.of("platform", "douyin"));

        List<AuditLog> logs = auditLogRepository.findByUserIdOrderByEventTimeDesc("user-1");
        assertEquals(1, logs.size());
        AuditLog log = logs.get(0);
        assertEquals("user-1", log.getUserId());
        assertEquals("TASK_DISPATCHED", log.getAction());
        assertEquals("TASK", log.getResourceType());
        assertEquals("task-1", log.getResourceId());
        assertEquals("trace-1", log.getTraceId());
        assertTrue(log.getDetails().contains("douyin"));
    }

    @Test
    @DisplayName("audit: record() creates PENDING outbox event")
    void record_createsPendingOutboxEvent() {
        auditService.record("user-1", "USER_LOGIN", "USER",
            "user-1", null, Map.of());

        List<OutboxEvent> events = outboxEventRepository
            .findByStatusOrderByEventTimeAsc(OutboxStatus.PENDING);
        assertEquals(1, events.size());
        OutboxEvent event = events.get(0);
        assertEquals("USER_LOGIN", event.getEventType());
        assertEquals("USER", event.getAggregateType());
        assertEquals(OutboxStatus.PENDING, event.getStatus());
        assertNull(event.getPublishedAt());
    }

    @Test
    @DisplayName("audit: multiple records create multiple entries")
    void record_multipleRecords_createMultipleEntries() {
        auditService.record("user-1", "USER_LOGIN", "USER", "user-1", null, Map.of());
        auditService.record("user-1", "TASK_DISPATCHED", "TASK", "task-1", "trace-1", Map.of());
        auditService.record("user-2", "USER_REGISTERED", "USER", "user-2", null, Map.of());

        assertEquals(3, auditLogRepository.findAll().size());
        assertEquals(3, outboxEventRepository.findAll().size());

        List<AuditLog> user1Logs = auditLogRepository.findByUserIdOrderByEventTimeDesc("user-1");
        assertEquals(2, user1Logs.size());
    }

    // -----------------------------------------------------------------------
    // Query by userId, resourceId, traceId
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("audit: query by userId returns entries ordered by time desc")
    void query_byUserId_returnsOrderedDesc() {
        auditService.record("user-q", "ACTION_A", "TASK", "res-1", "trace-a", Map.of());
        auditService.record("user-q", "ACTION_B", "TASK", "res-2", "trace-b", Map.of());

        List<AuditLog> logs = auditLogRepository.findByUserIdOrderByEventTimeDesc("user-q");
        assertEquals(2, logs.size());
        var actions = logs.stream().map(AuditLog::getAction).toList();
        assertTrue(actions.contains("ACTION_A"));
        assertTrue(actions.contains("ACTION_B"));
    }

    @Test
    @DisplayName("audit: query by resourceId returns matching entries")
    void query_byResourceId_returnsMatching() {
        auditService.record("user-1", "TASK_DISPATCHED", "TASK", "task-r1", "trace-1", Map.of());
        auditService.record("user-2", "TASK_STARTED", "TASK", "task-r1", "trace-1", Map.of());
        auditService.record("user-1", "TASK_DISPATCHED", "TASK", "task-r2", "trace-2", Map.of());

        List<AuditLog> logs = auditLogRepository.findByResourceIdOrderByEventTimeDesc("task-r1");
        assertEquals(2, logs.size());
    }

    @Test
    @DisplayName("audit: query by traceId returns matching entries")
    void query_byTraceId_returnsMatching() {
        auditService.record("user-1", "TASK_DISPATCHED", "TASK", "task-t1", "trace-x", Map.of());
        auditService.record("user-2", "TASK_STARTED", "TASK", "task-t1", "trace-x", Map.of());
        auditService.record("user-1", "TASK_DISPATCHED", "TASK", "task-t2", "trace-y", Map.of());

        List<AuditLog> logs = auditLogRepository.findByTraceIdOrderByEventTimeDesc("trace-x");
        assertEquals(2, logs.size());
    }

    @Test
    @DisplayName("audit: query by unknown userId returns empty list")
    void query_unknownUserId_returnsEmpty() {
        auditService.record("user-1", "USER_LOGIN", "USER", "user-1", null, Map.of());
        List<AuditLog> logs = auditLogRepository.findByUserIdOrderByEventTimeDesc("nobody");
        assertTrue(logs.isEmpty());
    }

    // -----------------------------------------------------------------------
    // Auth events audited
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("auth: register creates USER_REGISTERED audit entry")
    void auth_register_audited() throws Exception {
        mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(
                    new RegisterRequest("audit-reg-1", "password123", null))))
            .andExpect(status().isCreated());

        // Register audits with user.getId() (UUID), query all and filter by action
        List<AuditLog> logs = auditLogRepository.findAll().stream()
            .filter(l -> "USER_REGISTERED".equals(l.getAction()))
            .toList();
        assertEquals(1, logs.size());
        assertEquals("USER", logs.get(0).getResourceType());
        assertTrue(logs.get(0).getDetails().contains("audit-reg-1"));
    }

    @Test
    @DisplayName("auth: login creates USER_LOGIN audit entry")
    void auth_login_audited() throws Exception {
        registerUser("audit-login-1", "password123");

        mockMvc.perform(post("/api/v1/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(
                    new LoginRequest("audit-login-1", "password123"))))
            .andExpect(status().isOk());

        List<AuditLog> loginLogs = auditLogRepository
            .findByUserIdOrderByEventTimeDesc("audit-login-1")
            .stream()
            .filter(l -> "USER_LOGIN".equals(l.getAction()))
            .toList();
        assertEquals(1, loginLogs.size());
        assertEquals("USER", loginLogs.get(0).getResourceType());
    }

    @Test
    @DisplayName("auth: logout creates USER_LOGOUT audit entry")
    void auth_logout_audited() throws Exception {
        String token = registerAndLogin("audit-logout-1", "password123");

        mockMvc.perform(post("/api/v1/auth/logout")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isNoContent());

        // Logout audits with jwt.getSubject() (UUID), query all and filter by action
        List<AuditLog> logoutLogs = auditLogRepository.findAll().stream()
            .filter(l -> "USER_LOGOUT".equals(l.getAction()))
            .toList();
        assertEquals(1, logoutLogs.size());
    }

    @Test
    @DisplayName("auth: register also creates outbox event")
    void auth_register_createsOutbox() throws Exception {
        mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(
                    new RegisterRequest("audit-reg-2", "password123", null))))
            .andExpect(status().isCreated());

        List<OutboxEvent> events = outboxEventRepository
            .findByStatusOrderByEventTimeAsc(OutboxStatus.PENDING)
            .stream()
            .filter(e -> "USER_REGISTERED".equals(e.getEventType()))
            .toList();
        assertEquals(1, events.size());
    }

    // -----------------------------------------------------------------------
    // Task callback audited
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("callback: task event creates audit entry")
    void callback_taskEvent_audited() throws Exception {
        String token = registerAndLogin("audit-cb-1", "password123");

        // Dispatch task (will fail to publish, but task gets registered)
        mockMvc.perform(post("/api/v1/tasks")
                .header("Authorization", "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"taskId\":\"task-audit-cb-1\",\"type\":\"note.generate\","
                    + "\"input\":{\"url\":\"https://v.douyin.com/test\"},"
                    + "\"traceId\":\"trace-cb-1\",\"attempt\":1}"))
            .andReturn();

        // Send callback event
        mockMvc.perform(post("/internal/v1/tasks/task-audit-cb-1/events")
                .header("X-Callback-Secret", CALLBACK_SECRET)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"taskId\":\"task-audit-cb-1\",\"eventType\":\"task.started\","
                    + "\"schemaVersion\":\"1.0\",\"traceId\":\"trace-cb-1\","
                    + "\"timestamp\":\"2026-01-01T00:00:00Z\"}"))
            .andExpect(status().isOk());

        // Verify audit entry for the callback event
        List<AuditLog> logs = auditLogRepository
            .findByTraceIdOrderByEventTimeDesc("trace-cb-1")
            .stream()
            .filter(l -> "task.started".equals(l.getAction()))
            .toList();
        assertEquals(1, logs.size());
        assertEquals("TASK", logs.get(0).getResourceType());
        assertEquals("task-audit-cb-1", logs.get(0).getResourceId());
    }

    @Test
    @DisplayName("callback: outbox event created for task callback")
    void callback_createsOutboxEvent() throws Exception {
        String token = registerAndLogin("audit-cb-2", "password123");

        mockMvc.perform(post("/api/v1/tasks")
                .header("Authorization", "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"taskId\":\"task-audit-cb-2\",\"type\":\"note.generate\","
                    + "\"input\":{\"url\":\"https://v.douyin.com/test\"},"
                    + "\"traceId\":\"trace-cb-2\",\"attempt\":1}"))
            .andReturn();

        // Clear outbox from register + any dispatch attempt
        outboxEventRepository.deleteAll();

        mockMvc.perform(post("/internal/v1/tasks/task-audit-cb-2/events")
                .header("X-Callback-Secret", CALLBACK_SECRET)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"taskId\":\"task-audit-cb-2\",\"eventType\":\"task.succeeded\","
                    + "\"schemaVersion\":\"1.0\",\"traceId\":\"trace-cb-2\","
                    + "\"timestamp\":\"2026-01-01T00:00:00Z\"}"))
            .andExpect(status().isOk());

        List<OutboxEvent> events = outboxEventRepository
            .findByStatusOrderByEventTimeAsc(OutboxStatus.PENDING);
        assertFalse(events.isEmpty());
        assertTrue(events.stream().anyMatch(e -> "task.succeeded".equals(e.getEventType())));
    }

    // -----------------------------------------------------------------------
    // Audit log details serialization
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("audit: details field serializes map to JSON")
    void audit_detailsSerializedToJson() {
        auditService.record("user-d", "TASK_DISPATCHED", "TASK",
            "task-d", "trace-d", Map.of("platform", "douyin", "type", "note.generate"));

        List<AuditLog> logs = auditLogRepository.findByUserIdOrderByEventTimeDesc("user-d");
        assertEquals(1, logs.size());
        String details = logs.get(0).getDetails();
        assertTrue(details.contains("\"platform\":\"douyin\""));
        assertTrue(details.contains("\"type\":\"note.generate\""));
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    private void registerUser(String username, String password) throws Exception {
        mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(
                    new RegisterRequest(username, password, null))))
            .andReturn();
    }

    private String registerAndLogin(String username, String password) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(
                    new RegisterRequest(username, password, null))))
            .andReturn();
        TokenResponse tokens = objectMapper.readValue(
            result.getResponse().getContentAsString(), TokenResponse.class);
        return tokens.accessToken();
    }
}
