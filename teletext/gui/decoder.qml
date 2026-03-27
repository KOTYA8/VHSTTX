import QtQuick 2.12
import QtQuick.Controls 2.12
import QtGraphicalEffects 1.12


Rectangle {
    property real zoom: 2
    property bool doubleheight: true
    property bool doublewidth: true
    property bool flashenabled: true
    property string localcodepage: ""
    property bool forcecodepage: false
    property int pagecodepage: 0
    property real horizontalScale: 0.95
    property real borderSize: 6 * zoom
    property bool crteffect: true
    property bool flashsrc: true
    property bool reveal: false
    property bool fullscreenmode: false
    property bool fullscreenstretch: false
    property int viewportwidth: naturalTeletextWidth + effectiveBorderSize * 2
    property int viewportheight: naturalTeletextHeight + effectiveBorderSize * 2
    readonly property real effectiveBorderSize: fullscreenmode ? 0 : borderSize
    readonly property real naturalTeletextWidth: 40 * 8 * zoom * horizontalScale
    readonly property real naturalTeletextHeight: 250 * zoom
    readonly property real viewportInnerWidth: Math.max(0, width - effectiveBorderSize * 2)
    readonly property real viewportInnerHeight: Math.max(0, height - effectiveBorderSize * 2)
    readonly property real contentWidth: fullscreenmode && !fullscreenstretch
        ? Math.min(viewportInnerWidth, viewportInnerHeight * 4 / 3)
        : viewportInnerWidth
    readonly property real contentHeight: fullscreenmode && !fullscreenstretch
        ? Math.min(viewportInnerHeight, viewportInnerWidth * 3 / 4)
        : viewportInnerHeight
    readonly property real teletextScaleX: fullscreenmode ? contentWidth / naturalTeletextWidth : 1.0
    readonly property real teletextScaleY: fullscreenmode
        ? (fullscreenstretch
            ? contentHeight / naturalTeletextHeight
            : Math.min(contentWidth / naturalTeletextWidth, contentHeight / naturalTeletextHeight))
        : 1.0
    width: fullscreenmode ? viewportwidth : naturalTeletextWidth + effectiveBorderSize * 2
    height: fullscreenmode ? viewportheight : naturalTeletextHeight + effectiveBorderSize * 2
    border.width: effectiveBorderSize
    border.color: "black"
    color: "black"

    Item {
        id: viewport
        width: viewportInnerWidth
        height: viewportInnerHeight
        x: effectiveBorderSize
        y: effectiveBorderSize
        clip: true

        Item {
            id: contentFrame
            width: fullscreenmode ? contentWidth : naturalTeletextWidth
            height: fullscreenmode ? contentHeight : naturalTeletextHeight
            anchors.centerIn: parent

            Item {
                id: teletextFrame
                width: naturalTeletextWidth
                height: naturalTeletextHeight
                anchors.centerIn: parent
                transform: Scale {
                    origin.x: teletextFrame.width / 2
                    origin.y: teletextFrame.height / 2
                    xScale: teletextScaleX
                    yScale: teletextScaleY
                }

                Column {
                    id: teletext
                    objectName: "teletext"
                    width: naturalTeletextWidth
                    height: naturalTeletextHeight
                    clip: true

                    Repeater {
                        objectName: "rows"
                        model: 25

                        Row {
                            property int rowheight: 1
                            property bool rowrendered: true

                            Repeater {
                                objectName: "cols"
                                model: 40

                                Item {
                                    property string c: "X"
                                    property int bg: 1
                                    property int fg: 7
                                    property bool dw: false
                                    property bool dh: false
                                    property bool flash: false
                                    property bool mosaic: false
                                    property bool solid: true
                                    property bool boxed: false
                                    property bool conceal: false
                                    property bool rendered: true
                                    height: 10 * zoom
                                    width: 8 * zoom * horizontalScale

                                    Rectangle {
                                        height: rowheight * 10 * zoom
                                        width: (dw ? 2 : 1) * 8 * zoom * horizontalScale
                                        clip: true
                                        visible: rowrendered && rendered
                                        color: ttpalette[bg]

                                        Text {
                                            renderType: Text.NativeRendering
                                            anchors.top: parent.top
                                            anchors.horizontalCenter: parent.horizontalCenter
                                            color: ttpalette[fg]
                                            text: c
                                            font: ttfonts[(mosaic && solid && text[0] > "\ue000") ? 1 : 0][dw ? 1 : 0][dh ? 1 : 0]
                                            visible: ((!flash) || flashsrc) && (conceal ? reveal : true)
                                        }
                                    }
                                }
                            }
                        }
                    }
                    layer.enabled: crteffect && (zoom > 1)
                    layer.effect: ShaderEffect {
                        fragmentShader: "
                                uniform lowp sampler2D source;
                                uniform lowp float qt_Opacity;
                                varying highp vec2 qt_TexCoord0;
                                varying lowp vec3 qt_FragCoord0;
                                void main() {
                                    lowp vec4 tex = texture2D(source, qt_TexCoord0);
                                    int zoom = " + zoom + ";
                                    int row = int(gl_FragCoord.y) % zoom;
                                    gl_FragColor = (0 < row && (row < 2 || row < (zoom-1))) ? tex : tex*0.6;
                                }
                            "
                    }
                }
            }
        }
    }

    layer.enabled: crteffect && (zoom > 1)
    layer.effect: GaussianBlur {
        radius: 0.75 * zoom
    }

    SequentialAnimation on flashsrc {
        loops: -1
        running: true
        PropertyAction { value: false }
        PauseAnimation { duration: 333 }
        PropertyAction { value: true }
        PauseAnimation { duration: 1000 }
    }
}
