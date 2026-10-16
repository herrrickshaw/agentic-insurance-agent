@POL-3
Feature: Premium payment reconciliation
  Scenario Outline: Policy status after a payment
    Given a policy with an invoiced premium of 6000
    When a payment of <paid> is received
    Then the policy status is "<status>"
    And the payment status is "<payment>"

    Examples:
      | paid | status          | payment |
      | 6000 | active          | paid    |
      | 2500 | pending_payment | partial |
