package com.skyblock.sniper.client;

import com.mojang.blaze3d.platform.InputConstants;
import net.fabricmc.fabric.api.client.keymapping.v1.KeyMappingHelper;
import net.minecraft.client.KeyMapping;
import net.minecraft.resources.Identifier;
import org.lwjgl.glfw.GLFW;

public class ModKeybindings {
    public static final KeyMapping.Category CATEGORY = KeyMapping.Category.register(
            Identifier.fromNamespaceAndPath("ah_sniper", "main")
    );

    public static KeyMapping toggleAutoInspectKey;

    public static void register() {
        toggleAutoInspectKey = KeyMappingHelper.registerKeyMapping(new KeyMapping(
                "key.ah_sniper.toggle_auto_inspect",
                InputConstants.Type.KEYSYM,
                GLFW.GLFW_KEY_SEMICOLON,
                CATEGORY
        ));
    }

    public static boolean wasPressed() {
        return toggleAutoInspectKey != null && toggleAutoInspectKey.consumeClick();
    }
}
