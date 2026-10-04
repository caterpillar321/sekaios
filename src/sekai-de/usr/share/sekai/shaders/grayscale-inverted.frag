#version 320 es
// SekaiOS 색 필터 — 회색조 반전
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;
void main() {
    vec4 c = texture(tex, v_texcoord);
    float y = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
    fragColor = vec4(vec3(1.0 - y), c.a);
}
