import * as SecureStore from "expo-secure-store";

import type { SecretStorage } from "./session";

const OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export class ExpoSecureSecretStorage implements SecretStorage {
  getItem(key: string): Promise<string | null> {
    return SecureStore.getItemAsync(key, OPTIONS);
  }

  async setItem(key: string, value: string): Promise<void> {
    await SecureStore.setItemAsync(key, value, OPTIONS);
  }

  async deleteItem(key: string): Promise<void> {
    await SecureStore.deleteItemAsync(key, OPTIONS);
  }
}
