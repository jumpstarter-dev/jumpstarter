package dev.jumpstarter.client;

import com.google.protobuf.ListValue;
import com.google.protobuf.NullValue;
import com.google.protobuf.Struct;
import com.google.protobuf.Value;
import org.jetbrains.annotations.NotNull;
import org.jetbrains.annotations.Nullable;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Encodes Java objects to {@link com.google.protobuf.Value} and decodes back.
 *
 * <p>Supports: null, boolean, numbers, strings, lists, and maps (String keys).
 */
public final class ValueCodec {

    private ValueCodec() {}

    /**
     * Encode a Java object to a protobuf {@link Value}.
     *
     * @param obj the object to encode (null, Boolean, Number, String, List, or Map)
     * @return the encoded Value
     * @throws IllegalArgumentException if the object type is not supported
     */
    @NotNull
    public static Value encode(@Nullable Object obj) {
        if (obj == null) {
            return Value.newBuilder().setNullValue(NullValue.NULL_VALUE).build();
        }
        if (obj instanceof Boolean b) {
            return Value.newBuilder().setBoolValue(b).build();
        }
        if (obj instanceof Number n) {
            return Value.newBuilder().setNumberValue(n.doubleValue()).build();
        }
        if (obj instanceof String s) {
            return Value.newBuilder().setStringValue(s).build();
        }
        if (obj instanceof List<?> list) {
            ListValue.Builder lb = ListValue.newBuilder();
            for (Object item : list) {
                lb.addValues(encode(item));
            }
            return Value.newBuilder().setListValue(lb).build();
        }
        if (obj instanceof Map<?, ?> map) {
            Struct.Builder sb = Struct.newBuilder();
            for (Map.Entry<?, ?> entry : map.entrySet()) {
                sb.putFields(String.valueOf(entry.getKey()), encode(entry.getValue()));
            }
            return Value.newBuilder().setStructValue(sb).build();
        }
        throw new IllegalArgumentException("Unsupported type for Value encoding: " + obj.getClass().getName());
    }

    /**
     * Decode a protobuf {@link Value} to a Java object.
     *
     * @param value the Value to decode
     * @return the decoded object (null, Boolean, Double, String, List, or Map)
     */
    @Nullable
    public static Object decode(@NotNull Value value) {
        return switch (value.getKindCase()) {
            case NULL_VALUE -> null;
            case BOOL_VALUE -> value.getBoolValue();
            case NUMBER_VALUE -> value.getNumberValue();
            case STRING_VALUE -> value.getStringValue();
            case LIST_VALUE -> {
                List<Object> list = new ArrayList<>();
                for (Value v : value.getListValue().getValuesList()) {
                    list.add(decode(v));
                }
                yield list;
            }
            case STRUCT_VALUE -> {
                Map<String, Object> map = new LinkedHashMap<>();
                for (Map.Entry<String, Value> entry : value.getStructValue().getFieldsMap().entrySet()) {
                    map.put(entry.getKey(), decode(entry.getValue()));
                }
                yield map;
            }
            case KIND_NOT_SET -> null;
        };
    }
}
