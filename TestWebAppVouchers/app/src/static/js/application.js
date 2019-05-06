
function redeem() {
    method = $("input[name='method']:checked").val();
    type = $("input[name='type']:checked").val();
    code = $("#code").val();

    console.log("Going to redeem voucher " + code + " using method " + method + " and type " + type);
    if (type === 'multi') {       
        redeem_url = "/redeem_multi/" + encodeURI(method) + "/" + encodeURI(code);
    } else {
        redeem_url = "/redeem/" + encodeURI(method) + "/" + encodeURI(code);
    }

    $.ajax({
        url: redeem_url,
        method: 'POST',
        dataType: 'json',
        contentType: "application/json",
        data: JSON.stringify({"do_redeem":"true"}),
        success: function (the_json) {
            console.log(the_json);
            $("#msg").text("Success: " + JSON.stringify(the_json));
        },
        error: function() {
            $("#msg").text("Failed to redeem: " + code + "");
        },
    });
}

function reset() {
    console.log("Resetting database");
    reset_url = "/reset"

    $.ajax({
        url: reset_url,
        method: 'POST',
        success: function () {
            $("#msg").text("Resetted database!");
        },
        error: function() {
            $("#msg").text("Failed resetting database!");
        },
    });
}

$(document).ready(function() {
    $("#redeem").click(function () {
        redeem();
    });
    $("#reset").click(function () {
        reset();
    });
});

