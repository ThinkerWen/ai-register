from util import config as config_utils
from util import cpa as cpa_utils
from util import get_logger, setup_logger
from util import mail as mail_utils
from util import model as model_utils

setup_logger()
logger = get_logger("main")


def main():
    config = config_utils.get_register_config(logger=logger)
    mail_info = mail_utils.get_mail_provider_info(config)
    mail_ok, mail_err = mail_utils.validate_mail_provider_config(config)
    model_info = model_utils.get_model_provider_info(config)
    model_ok, model_err = model_utils.validate_model_provider_config(config)
    cpa_ok, cpa_msg = cpa_utils.validate_cpa_config(config)

    if not mail_ok:
        logger.warning(f"邮箱 provider 配置不完整: {mail_err}")
        logger.warning("请检查 config.yaml 的 mail_provider / mail_providers 配置")
    if not model_ok:
        logger.warning(f"模型 provider 配置不完整: {model_err}")
        logger.warning("请检查 config.yaml 的 model_provider / providers 配置")
    if not cpa_ok:
        logger.warning(f"CPA 配置不完整: {cpa_msg}")
    else:
        logger.debug(f"CPA 配置检查: {cpa_msg}")

    logger.info(f"配置并发: {config['concurrency']}")
    logger.info(f"配置总数: {config['total_accounts']}")
    logger.info(f"配置代理: {config['proxy'] or '无'}")
    logger.info(f"模型 provider: {model_info['name']}")
    logger.info(f"邮箱 provider: {mail_info['name']} {mail_info['api_base']}")

    provider = model_utils.create_model_provider(config)
    provider.run_batch(
        total_accounts=config.get("total_accounts"),
        max_workers=config.get("concurrency"),
        proxy=config.get("proxy"),
    )


if __name__ == "__main__":
    main()
