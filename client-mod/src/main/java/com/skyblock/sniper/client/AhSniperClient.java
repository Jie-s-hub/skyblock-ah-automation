package com.skyblock.sniper.client;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.mojang.blaze3d.platform.InputConstants;
import com.skyblock.sniper.client.gui.FlipScreen;
import net.fabricmc.api.ClientModInitializer;
import net.fabricmc.api.EnvType;
import net.fabricmc.api.Environment;
import net.fabricmc.fabric.api.client.event.lifecycle.v1.ClientTickEvents;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.minecraft.client.KeyMapping;
import net.minecraft.client.Minecraft;
import org.lwjgl.glfw.GLFW;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.concurrent.*;
import java.util.concurrent.atomic.AtomicBoolean;

@Environment(EnvType.CLIENT)
public class AhSniperClient implements ClientModInitializer {
    public static final String MOD_ID = "ah_sniper";
    public static final Logger LOGGER = LoggerFactory.getLogger(MOD_ID);
    public static final String WS_URI = "ws://127.0.0.1:8765";
    public static final int MAX_SLIDING_WINDOW_SIZE = 30;

    // Sliding window buffer storing incoming snipes
    public static final CopyOnWriteArrayList<JsonObject> ACTIVE_FLIPS = new CopyOnWriteArrayList<>();
    public static final AtomicBoolean IS_CONNECTED = new AtomicBoolean(false);

    private static KeyMapping keyOpenGui;
    private static WebSocket webSocket;
    private static final ScheduledExecutorService RECONNECT_EXECUTOR = Executors.newSingleThreadScheduledExecutor(r -> {
        Thread t = new Thread(r, "ah-sniper-reconnect");
        t.setDaemon(true);
        return t;
    });

    @Override
    public void onInitializeClient() {
        LOGGER.info("[AH Sniper] Initializing Fabric Client Mod...");

        // Register keybindings
        ModKeybindings.register();

        keyOpenGui = KeyMappingHelper.registerKeyMapping(new KeyMapping(
                "key.ah_sniper.open",
                InputConstants.Type.KEYSYM,
                GLFW.GLFW_KEY_K,
                ModKeybindings.CATEGORY
        ));

        // Connect to local WebSocket bridge
        initWebSocketConnection();

        // Register client tick event to open FlipScreen and toggle Auto-Inspect on keybind press
        ClientTickEvents.END_CLIENT_TICK.register(client -> {
            while (keyOpenGui.consumeClick()) {
                if (client.player != null) {
                    client.setScreen(new FlipScreen());
                }
            }

            while (ModKeybindings.toggleAutoInspectKey.consumeClick()) {
                AutoInspectManager.toggle(client);
            }
        });

        LOGGER.info("[AH Sniper] Successfully initialized. Keybinds [K] and [;] registered under Hypixel AH Sniper.");
    }

    public static synchronized void initWebSocketConnection() {
        HttpClient client = HttpClient.newBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .build();

        client.newWebSocketBuilder()
                .connectTimeout(Duration.ofSeconds(3))
                .buildAsync(URI.create(WS_URI), new WebSocketListener())
                .thenAccept(ws -> {
                    webSocket = ws;
                    IS_CONNECTED.set(true);
                    LOGGER.info("[AH Sniper] Connected to Local WebSocket at {}", WS_URI);
                })
                .exceptionally(throwable -> {
                    IS_CONNECTED.set(false);
                    scheduleReconnect();
                    return null;
                });
    }

    private static void scheduleReconnect() {
        RECONNECT_EXECUTOR.schedule(() -> {
            if (!IS_CONNECTED.get()) {
                LOGGER.debug("[AH Sniper] Attempting to reconnect to {}...", WS_URI);
                initWebSocketConnection();
            }
        }, 3, TimeUnit.SECONDS);
    }

    private static class WebSocketListener implements WebSocket.Listener {
        private final StringBuilder buffer = new StringBuilder();

        @Override
        public void onOpen(WebSocket ws) {
            IS_CONNECTED.set(true);
            ws.request(1);
        }

        @Override
        public CompletionStage<?> onText(WebSocket ws, CharSequence data, boolean last) {
            buffer.append(data);
            if (last) {
                String completeMessage = buffer.toString();
                buffer.setLength(0);
                processIncomingFlip(completeMessage);
            }
            ws.request(1);
            return null;
        }

        @Override
        public CompletionStage<?> onClose(WebSocket ws, int statusCode, String reason) {
            IS_CONNECTED.set(false);
            LOGGER.warn("[AH Sniper] WebSocket closed: {}. Scheduling reconnect...", reason);
            scheduleReconnect();
            return null;
        }

        @Override
        public void onError(WebSocket ws, Throwable error) {
            IS_CONNECTED.set(false);
            LOGGER.warn("[AH Sniper] WebSocket error: {}. Scheduling reconnect...", error.getMessage());
            scheduleReconnect();
        }
    }

    public static void processIncomingFlip(String jsonPayload) {
        try {
            JsonObject flip = JsonParser.parseString(jsonPayload).getAsJsonObject();
            String uuid = flip.has("uuid") ? flip.get("uuid").getAsString() : "";

            if (!uuid.isEmpty()) {
                // Deduplicate: remove older listing with identical auction UUID
                ACTIVE_FLIPS.removeIf(existing -> existing.has("uuid") && uuid.equals(existing.get("uuid").getAsString()));
            }

            // Insert new flip at front of sliding window
            ACTIVE_FLIPS.add(0, flip);

            // Enforce 30-item sliding window cap
            while (ACTIVE_FLIPS.size() > MAX_SLIDING_WINDOW_SIZE) {
                ACTIVE_FLIPS.remove(ACTIVE_FLIPS.size() - 1);
            }

            // Forward to WebSocketClientHandler for Auto-Inspect evaluation
            WebSocketClientHandler.onMessage(jsonPayload);
        } catch (Exception err) {
            LOGGER.error("[AH Sniper] Failed to parse incoming flip payload: {}", err.getMessage());
        }
    }
}
