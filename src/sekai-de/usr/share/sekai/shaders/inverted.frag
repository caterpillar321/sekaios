#version 320 es
// SekaiOS 색 필터 — 반전
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;
void main() {
    vec4 c = texture(tex, v_texcoord);
    fragColor = vec4(1.0 - c.rgb, c.a);
}
