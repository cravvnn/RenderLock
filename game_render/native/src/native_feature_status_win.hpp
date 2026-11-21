// Included inside bridge scope; worker-owned status, never engine memory writes.
#pragma once
CapabilitySnapshot gCapabilities;

static void publish_native_capabilities() noexcept {
    if (!gMemory)
        return;
    auto value = gCapabilities;
    value.game_pid = GetCurrentProcessId();
    auto out = gMemory + kCapabilitiesOffset;
    auto sequence = reinterpret_cast<volatile LONG*>(out + 8);
    const auto old = InterlockedCompareExchange(sequence, 0, 0);
    const auto even = (old & 1) ? old + 1 : old;
    InterlockedExchange(sequence, even + 1);
    value.sequence = even + 2;
    std::memcpy(out, value.magic, 8);
    std::memcpy(out + 12, reinterpret_cast<const unsigned char*>(&value) + 12, sizeof(value) - 12);
    MemoryBarrier();
    InterlockedExchange(sequence, even + 2);
}
