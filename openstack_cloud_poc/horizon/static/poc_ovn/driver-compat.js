/* Scoped to this POC's exclusively OVN provider. Vendor files are untouched. */
(function () {
  'use strict';
  angular.module('horizon.dashboard.project.lbaasv2').config(['$provide', function ($provide) {
    $provide.decorator('horizon.dashboard.project.lbaasv2.workflow.model', ['$delegate', function (model) {
      model.methods = ['SOURCE_IP_PORT'];
      model.listenerProtocols = ['TCP'];
      model.poolProtocols = ['TCP'];
      model.monitorTypes = ['TCP'];
      model.types = [];
      return model;
    }]);
  }]);
}());
