package dev.jumpstarter.client;

import com.google.protobuf.Value;
import org.junit.jupiter.api.Test;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.*;

class ValueCodecTest {

    @Test
    void encodeDecodeNull() {
        Value v = ValueCodec.encode(null);
        assertNull(ValueCodec.decode(v));
    }

    @Test
    void encodeDecodeBoolean() {
        Value v = ValueCodec.encode(true);
        assertEquals(true, ValueCodec.decode(v));

        v = ValueCodec.encode(false);
        assertEquals(false, ValueCodec.decode(v));
    }

    @Test
    void encodeDecodeNumber() {
        Value v = ValueCodec.encode(42);
        assertEquals(42.0, ValueCodec.decode(v));

        v = ValueCodec.encode(3.14);
        assertEquals(3.14, ValueCodec.decode(v));
    }

    @Test
    void encodeDecodeString() {
        Value v = ValueCodec.encode("hello");
        assertEquals("hello", ValueCodec.decode(v));
    }

    @Test
    void encodeDecodeList() {
        List<Object> input = List.of("a", 1, true);
        Value v = ValueCodec.encode(input);
        Object result = ValueCodec.decode(v);

        assertInstanceOf(List.class, result);
        @SuppressWarnings("unchecked")
        List<Object> list = (List<Object>) result;
        assertEquals(3, list.size());
        assertEquals("a", list.get(0));
        assertEquals(1.0, list.get(1));
        assertEquals(true, list.get(2));
    }

    @Test
    void encodeDecodeMap() {
        Map<String, Object> input = Map.of("key", "value", "num", 42);
        Value v = ValueCodec.encode(input);
        Object result = ValueCodec.decode(v);

        assertInstanceOf(Map.class, result);
        @SuppressWarnings("unchecked")
        Map<String, Object> map = (Map<String, Object>) result;
        assertEquals("value", map.get("key"));
        assertEquals(42.0, map.get("num"));
    }

    @Test
    void encodeDecodeNested() {
        Map<String, Object> input = Map.of(
                "devices", List.of(
                        Map.of("name", "power", "enabled", true),
                        Map.of("name", "serial", "enabled", false)
                )
        );

        Value v = ValueCodec.encode(input);
        Object result = ValueCodec.decode(v);

        assertInstanceOf(Map.class, result);
        @SuppressWarnings("unchecked")
        Map<String, Object> map = (Map<String, Object>) result;
        assertInstanceOf(List.class, map.get("devices"));
    }

    @Test
    void encodeUnsupportedTypeThrows() {
        assertThrows(IllegalArgumentException.class, () ->
                ValueCodec.encode(new Object()));
    }
}
