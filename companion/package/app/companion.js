Ext.ns("US3000.Companion");

Ext.define("US3000.Companion.Application", {
    extend: "SYNO.SDS.AppInstance",
    appWindowName: "US3000.Companion.AppWindow",
    constructor: function () {
        this.callParent(arguments);
    }
});

Ext.define("US3000.Companion.AppWindow", {
    extend: "SYNO.SDS.AppWindow",
    layout: "fit",
    constructor: function (config) {
        this.callParent([Ext.apply({
            width: 1100,
            height: 760,
            minWidth: 720,
            minHeight: 520,
            items: [{
                xtype: "panel",
                border: false,
                html: '<iframe src="/webman/3rdparty/us3000-companion/index.html" ' +
                      'style="width:100%;height:100%;border:none;background:transparent;"></iframe>'
            }]
        }, config)]);
    }
});
