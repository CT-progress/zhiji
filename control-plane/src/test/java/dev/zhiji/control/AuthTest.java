package dev.zhiji.control;

import com.fasterxml.jackson.databind.ObjectMapper;
import dev.zhiji.control.dto.*;
import dev.zhiji.control.service.TokenRevocationService;
import org.junit.jupiter.api.*;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.AutoConfigureMockMvc;
import org.springframework.boot.test.context.SpringBootTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;
import org.springframework.test.web.servlet.MvcResult;

import static org.junit.jupiter.api.Assertions.*;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.*;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.*;

/**
 * Phase 6.2 auth tests: register, login, refresh, logout, me, RBAC, callback secret.
 */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.MOCK)
@AutoConfigureMockMvc
class AuthTest {

    @Autowired
    private MockMvc mockMvc;
    @Autowired
    private ObjectMapper objectMapper;
    @Autowired
    private TokenRevocationService revocationService;

    private static final String CALLBACK_SECRET = "zhiji-callback-dev-secret";

    @AfterEach
    void cleanup() {
        revocationService.clear();
    }

    // -----------------------------------------------------------------------
    // Registration
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("register: valid request returns 201 with tokens")
    void register_validRequest_returns201WithTokens() throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new RegisterRequest("testuser1", "password123", "Test User"))))
            .andExpect(status().isCreated())
            .andReturn();

        TokenResponse tokens = objectMapper.readValue(
            result.getResponse().getContentAsString(), TokenResponse.class);
        assertNotNull(tokens.accessToken());
        assertNotNull(tokens.refreshToken());
        assertEquals("Bearer", tokens.tokenType());
        assertEquals("testuser1", tokens.username());
        assertTrue(tokens.roles().contains("USER"));
    }

    @Test
    @DisplayName("register: duplicate username returns 409")
    void register_duplicateUsername_returns409() throws Exception {
        RegisterRequest req = new RegisterRequest("testuser2", "password123", null);
        mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(req)))
            .andExpect(status().isCreated());

        mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(req)))
            .andExpect(status().isConflict());
    }

    @Test
    @DisplayName("register: short password returns 400")
    void register_shortPassword_returns400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new RegisterRequest("testuser3", "short", null))))
            .andExpect(status().isBadRequest());
    }

    @Test
    @DisplayName("register: invalid username returns 400")
    void register_invalidUsername_returns400() throws Exception {
        mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new RegisterRequest("ab", "password123", null))))
            .andExpect(status().isBadRequest());
    }

    // -----------------------------------------------------------------------
    // Login
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("login: correct credentials return 200 with tokens")
    void login_correctCredentials_returns200() throws Exception {
        registerUser("loginuser1", "password123");

        mockMvc.perform(post("/api/v1/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new LoginRequest("loginuser1", "password123"))))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.accessToken").exists())
            .andExpect(jsonPath("$.refreshToken").exists())
            .andExpect(jsonPath("$.tokenType").value("Bearer"));
    }

    @Test
    @DisplayName("login: wrong password returns 401")
    void login_wrongPassword_returns401() throws Exception {
        registerUser("loginuser2", "password123");

        mockMvc.perform(post("/api/v1/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new LoginRequest("loginuser2", "wrongpass"))))
            .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("login: unknown user returns 401")
    void login_unknownUser_returns401() throws Exception {
        mockMvc.perform(post("/api/v1/auth/login")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new LoginRequest("nobody", "password123"))))
            .andExpect(status().isUnauthorized());
    }

    // -----------------------------------------------------------------------
    // Me (authenticated endpoint)
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("me: without token returns 401")
    void me_withoutToken_returns401() throws Exception {
        mockMvc.perform(get("/api/v1/auth/me"))
            .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("me: with valid token returns user info")
    void me_withValidToken_returnsUserInfo() throws Exception {
        String token = registerAndLogin("meuser1", "password123");

        mockMvc.perform(get("/api/v1/auth/me")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.username").value("meuser1"))
            .andExpect(jsonPath("$.roles[0]").value("USER"));
    }

    @Test
    @DisplayName("me: with invalid token returns 401")
    void me_withInvalidToken_returns401() throws Exception {
        mockMvc.perform(get("/api/v1/auth/me")
                .header("Authorization", "Bearer invalid.jwt.token"))
            .andExpect(status().isUnauthorized());
    }

    // -----------------------------------------------------------------------
    // Refresh
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("refresh: valid refresh token returns new token pair")
    void refresh_validToken_returnsNewTokens() throws Exception {
        TokenResponse tokens = registerUserAndGetTokens("refreshuser1", "password123");

        MvcResult result = mockMvc.perform(post("/api/v1/auth/refresh")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new RefreshRequest(tokens.refreshToken()))))
            .andExpect(status().isOk())
            .andReturn();

        TokenResponse newTokens = objectMapper.readValue(
            result.getResponse().getContentAsString(), TokenResponse.class);
        assertNotNull(newTokens.accessToken());
        assertNotEquals(tokens.accessToken(), newTokens.accessToken());
    }

    @Test
    @DisplayName("refresh: used refresh token is revoked (one-time use)")
    void refresh_usedToken_isRevoked() throws Exception {
        TokenResponse tokens = registerUserAndGetTokens("refreshuser2", "password123");

        // First refresh succeeds
        mockMvc.perform(post("/api/v1/auth/refresh")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new RefreshRequest(tokens.refreshToken()))))
            .andExpect(status().isOk());

        // Second refresh with same token fails (revoked)
        mockMvc.perform(post("/api/v1/auth/refresh")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new RefreshRequest(tokens.refreshToken()))))
            .andExpect(status().isUnauthorized());
    }

    // -----------------------------------------------------------------------
    // Logout (token revocation)
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("logout: revokes current token")
    void logout_revokesCurrentToken() throws Exception {
        String token = registerAndLogin("logoutuser1", "password123");

        // Token works before logout
        mockMvc.perform(get("/api/v1/auth/me")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isOk());

        // Logout
        mockMvc.perform(post("/api/v1/auth/logout")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isNoContent());

        // Token is now revoked
        mockMvc.perform(get("/api/v1/auth/me")
                .header("Authorization", "Bearer " + token))
            .andExpect(status().isUnauthorized());
    }

    // -----------------------------------------------------------------------
    // RBAC
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("RBAC: protected task endpoint requires authentication")
    void rbac_tasksRequiresAuth() throws Exception {
        mockMvc.perform(post("/api/v1/tasks")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{}"))
            .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("RBAC: admin endpoint requires ADMIN role")
    void rbac_adminRequiresAdminRole() throws Exception {
        String userToken = registerAndLogin("rbacuser1", "password123");

        mockMvc.perform(get("/api/v1/admin/users")
                .header("Authorization", "Bearer " + userToken))
            .andExpect(status().isForbidden());
    }

    // -----------------------------------------------------------------------
    // Callback secret
    // -----------------------------------------------------------------------

    @Test
    @DisplayName("callback: without secret returns 401")
    void callback_withoutSecret_returns401() throws Exception {
        mockMvc.perform(post("/internal/v1/tasks/task-1/events")
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"taskId\":\"task-1\",\"eventType\":\"task.started\",\"schemaVersion\":\"1.0\",\"traceId\":\"t1\",\"timestamp\":\"2026-01-01T00:00:00Z\"}"))
            .andExpect(status().isUnauthorized());
    }

    @Test
    @DisplayName("callback: with valid secret returns 200")
    void callback_withValidSecret_returns200() throws Exception {
        // Register a task first so the callback has a target
        String token = registerAndLogin("callbackuser1", "password123");
        mockMvc.perform(post("/api/v1/tasks")
                .header("Authorization", "Bearer " + token)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"taskId\":\"task-cb-1\",\"type\":\"note.generate\",\"input\":{\"url\":\"https://v.douyin.com/test\"},\"traceId\":\"trace-1\",\"attempt\":1}"))
            .andReturn();

        mockMvc.perform(post("/internal/v1/tasks/task-cb-1/events")
                .header("X-Callback-Secret", CALLBACK_SECRET)
                .contentType(MediaType.APPLICATION_JSON)
                .content("{\"taskId\":\"task-cb-1\",\"eventType\":\"task.started\",\"schemaVersion\":\"1.0\",\"traceId\":\"trace-1\",\"timestamp\":\"2026-01-01T00:00:00Z\"}"))
            .andExpect(status().isOk());
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    private void registerUser(String username, String password) throws Exception {
        mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new RegisterRequest(username, password, null))))
            .andReturn();
    }

    private String registerAndLogin(String username, String password) throws Exception {
        TokenResponse tokens = registerUserAndGetTokens(username, password);
        return tokens.accessToken();
    }

    private TokenResponse registerUserAndGetTokens(String username, String password) throws Exception {
        MvcResult result = mockMvc.perform(post("/api/v1/auth/register")
                .contentType(MediaType.APPLICATION_JSON)
                .content(objectMapper.writeValueAsString(new RegisterRequest(username, password, null))))
            .andReturn();
        return objectMapper.readValue(result.getResponse().getContentAsString(), TokenResponse.class);
    }
}
