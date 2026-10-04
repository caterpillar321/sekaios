#version 320 es
// SekaiOS 색 필터 — 청황 (청색약) 보정 (daltonize)
precision highp float;
in vec2 v_texcoord;
uniform sampler2D tex;
out vec4 fragColor;
void main() {
    vec4 c = texture(tex, v_texcoord);
    vec3 lms = vec3(17.8824 * c.r + 43.5161 * c.g + 4.11935 * c.b,
                    3.45565 * c.r + 27.1554 * c.g + 3.86714 * c.b,
                    0.0299566 * c.r + 0.184309 * c.g + 1.46709 * c.b);
    vec3 s = vec3(lms.x, lms.y, -0.395913 * lms.x + 0.801109 * lms.y);
    vec3 sim = vec3(0.0809444479 * s.x - 0.130504409 * s.y + 0.116721066 * s.z,
                    -0.0102485335 * s.x + 0.0540193266 * s.y - 0.113614708 * s.z,
                    -0.000365296938 * s.x - 0.00412161469 * s.y + 0.693511405 * s.z);
    vec3 err = c.rgb - sim;
    vec3 shift = vec3(0.0, 0.7 * err.r + err.g, 0.7 * err.r + err.b);
    fragColor = vec4(clamp(c.rgb + shift, 0.0, 1.0), c.a);
}
