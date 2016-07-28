from abc import ABCMeta

import numpy as np
from ConfigSpace import Configuration
from sklearn.pipeline import Pipeline
from sklearn.utils.validation import check_random_state, check_is_fitted

from .components.base import AutoSklearnChoice, AutoSklearnComponent
import autosklearn.pipeline.create_searchspace_util


class BasePipeline(Pipeline):
    """Base class for all pipeline objects.

    Notes
    -----
    This class should not be instantiated, only subclassed."""
    __metaclass__ = ABCMeta

    def __init__(self, config=None, pipeline=None, dataset_properties=None,
                 include=None, exclude=None, random_state=None):
        if pipeline is None:
            self.steps = self._get_pipeline()
        else:
            self.steps = pipeline

        if dataset_properties is None:
            self.dataset_properties_ = {}
        else:
            self.dataset_properties_ = dataset_properties

        if config is None:
            self.configuration_ = self.get_hyperparameter_search_space(
                dataset_properties=dataset_properties,
                include=include, exclude=exclude).get_default_configuration()
        else:
            cs = self.get_hyperparameter_search_space(
                dataset_properties=dataset_properties,
                include=include, exclude=exclude)
            if isinstance(config, dict):
                config = Configuration(cs, config)
            if cs != config.configuration_space:
                print(cs._children)
                print(config.configuration_space._children)
                import difflib
                diff = difflib.unified_diff(
                    str(cs).splitlines(),
                    str(config.configuration_space).splitlines())
                diff = '\n'.join(diff)
                raise ValueError('Configuration passed does not come from the '
                                 'same configuration space. Differences are: '
                                 '%s' % diff)
            self.configuration_ = config
        self.set_hyperparameters(self.configuration_)

        if random_state is None:
            self.random_state = check_random_state(1)
        else:
            self.random_state = check_random_state(random_state)

    def fit(self, X, y, fit_params=None, init_params=None):
        """Fit the selected algorithm to the training data.

        Parameters
        ----------
        X : array-like or sparse, shape = (n_samples, n_features)
            Training data. The preferred type of the matrix (dense or sparse)
            depends on the estimator selected.

        y : array-like
            Targets

        fit_params : dict
            See the documentation of sklearn.pipeline.Pipeline for formatting
            instructions.

        init_params : dict
            Pass arguments to the constructors of single methods. To pass
            arguments to only one of the methods (lets says the
            OneHotEncoder), seperate the class name from the argument by a ':'.

        Returns
        -------
        self : returns an instance of self.

        Raises
        ------
        NoModelException
            NoModelException is raised if fit() is called without specifying
            a classification algorithm first.
        """
        X, fit_params = self.pre_transform(X, y, fit_params=fit_params)
        self.fit_estimator(X, y, **fit_params)
        return self

    def pre_transform(self, X, y, fit_params=None, init_params=None):
        # TODO do something with the init params!
        # TODO actually, initialize the submodels only here?
        if fit_params is None or not isinstance(fit_params, dict):
            fit_params = dict()
        else:
            fit_params = {key.replace(":", "__"): value for key, value in
                          fit_params.items()}
        X, fit_params = self._pre_transform(X, y, **fit_params)
        return X, fit_params

    def fit_estimator(self, X, y, **fit_params):
        if fit_params is None:
            fit_params = {}
        self.steps[-1][-1].fit(X, y, **fit_params)
        return self

    def iterative_fit(self, X, y, n_iter=1, **fit_params):
        if fit_params is None:
            fit_params = {}
        self.steps[-1][-1].iterative_fit(X, y, n_iter=n_iter,
                                                   **fit_params)

    def estimator_supports_iterative_fit(self):
        return hasattr(self.steps[-1][-1], 'iterative_fit')

    def configuration_fully_fitted(self):
        check_is_fitted(self, 'pipeline_')
        return self.steps[-1][-1].configuration_fully_fitted()

    def predict(self, X, batch_size=None):
        """Predict the classes using the selected model.

        Parameters
        ----------
        X : array-like, shape = (n_samples, n_features)

        batch_size: int or None, defaults to None
            batch_size controls whether the pipeline will be
            called on small chunks of the data. Useful when calling the
            predict method on the whole array X results in a MemoryError.

        Returns
        -------
        array, shape=(n_samples,) if n_classes == 2 else (n_samples, n_classes)
            Returns the predicted values"""
        # TODO check if fit() was called before...

        if batch_size is None:
            return super(BasePipeline, self).predict(X).astype(self._output_dtype)
        else:
            if type(batch_size) is not int or batch_size <= 0:
                raise Exception("batch_size must be a positive integer")

            else:
                if self.num_targets == 1:
                    y = np.zeros((X.shape[0],), dtype=self._output_dtype)
                else:
                    y = np.zeros((X.shape[0], self.num_targets),
                                 dtype=self._output_dtype)

                # Copied and adapted from the scikit-learn GP code
                for k in range(max(1, int(np.ceil(float(X.shape[0]) /
                                                  batch_size)))):
                    batch_from = k * batch_size
                    batch_to = min([(k + 1) * batch_size, X.shape[0]])
                    y[batch_from:batch_to] = \
                        self.predict(X[batch_from:batch_to], batch_size=None)

                return y

    def set_hyperparameters(self, configuration=None, init_params=None):
        self.configuration = configuration

        for node_idx, n_ in enumerate(self.steps):
            node_name, node = n_

            sub_configuration_space = node.get_hyperparameter_search_space(
                dataset_properties=self.dataset_properties_
            )
            sub_config_dict = {}
            for param in configuration:
                if param.startswith('%s:' % node_name):
                    value = configuration[param]
                    new_name = param.replace('%s:' % node_name, '', 1)
                    sub_config_dict[new_name] = value

            sub_configuration = Configuration(sub_configuration_space,
                                              values=sub_config_dict)

            # TODO set hyperparameters of child objects!
            if isinstance(node, (AutoSklearnChoice, AutoSklearnComponent)):
                node.set_hyperparameters(sub_configuration)
            else:
                raise NotImplementedError('Not supported yet!')

        return self

    def get_hyperparameter_search_space(self, include=None, exclude=None,
                                        dataset_properties=None):
        """Return the configuration space for the CASH problem.

        This method should be called by the method
        get_hyperparameter_search_space of a subclass. After the subclass
        assembles a list of available estimators and preprocessor components,
        _get_hyperparameter_search_space can be called to do the work of
        creating the actual
        ConfigSpace.configuration_space.ConfigurationSpace object.

        Parameters
        ----------
        estimator_name : str
            Name of the estimator hyperparameter which will be used in the
            configuration space. For a classification task, this would be
            'classifier'.

        estimator_components : dict {name: component}
            Dictionary with all estimator components to be included in the
            configuration space.

        preprocessor_components : dict {name: component}
            Dictionary with all preprocessor components to be included in the
            configuration space. .

        always_active : list of str
            A list of components which will always be active in the pipeline.
            This is useful for components like imputation which have
            hyperparameters to be configured, but which do not have any parent.

        default_estimator : str
            Default value for the estimator hyperparameter.

        Returns
        -------
        cs : ConfigSpace.configuration_space.Configuration
            The configuration space describing the AutoSklearnClassifier.

        """
        raise NotImplementedError()

    def _get_hyperparameter_search_space(self, cs, dataset_properties, exclude,
                                         include, pipeline):
        if include is None:
            include = {}

        keys = [pair[0] for pair in pipeline]
        for key in include:
            if key not in keys:
                raise ValueError('Invalid key in include: %s; should be one '
                                 'of %s' % (key, keys))

        if exclude is None:
            exclude = {}

        keys = [pair[0] for pair in pipeline]
        for key in exclude:
            if key not in keys:
                raise ValueError('Invalid key in exclude: %s; should be one '
                                 'of %s' % (key, keys))

        if 'sparse' not in dataset_properties:
            # This dataset is probaby dense
            dataset_properties['sparse'] = False
        if 'signed' not in dataset_properties:
            # This dataset probably contains unsigned data
            dataset_properties['signed'] = False

        matches = autosklearn.pipeline.create_searchspace_util.get_match_array(
            pipeline, dataset_properties, include=include, exclude=exclude)

        # Now we have only legal combinations at this step of the pipeline
        # Simple sanity checks
        assert np.sum(matches) != 0, "No valid pipeline found."

        assert np.sum(matches) <= np.size(matches), \
            "'matches' is not binary; %s <= %d, %s" % \
            (str(np.sum(matches)), np.size(matches), str(matches.shape))

        # Iterate each dimension of the matches array (each step of the
        # pipeline) to see if we can add a hyperparameter for that step
        for node_idx, n_ in enumerate(pipeline):
            node_name, node = n_

            is_choice = isinstance(node, AutoSklearnChoice)

            # if the node isn't a choice we can add it immediately because it
            #  must be active (if it wouldn't, np.sum(matches) would be zero
            if not is_choice:
                cs.add_configuration_space(node_name,
                    node.get_hyperparameter_search_space(dataset_properties))
            # If the node isn't a choice, we have to figure out which of it's
            #  choices are actually legal choices
            else:
                choices_list = autosklearn.pipeline.create_searchspace_util.\
                    find_active_choices(matches, node, node_idx,
                                        dataset_properties,
                                        include.get(node_name),
                                        exclude.get(node_name))
                sub_config_space = node.get_hyperparameter_search_space(
                    dataset_properties, include=choices_list)
                cs.add_configuration_space(node_name, sub_config_space)

        # And now add forbidden parameter configurations
        # According to matches
        if np.sum(matches) < np.size(matches):
            cs = autosklearn.pipeline.create_searchspace_util.add_forbidden(
                conf_space=cs, pipeline=pipeline, matches=matches,
                dataset_properties=dataset_properties, include=include,
                exclude=exclude)

        return cs

    def __repr__(self):
        class_name = self.__class__.__name__

        configuration = {}
        self.configuration_._populate_values()
        for hp_name in self.configuration_:
            if self.configuration_[hp_name] is not None:
                configuration[hp_name] = self.configuration_[hp_name]

        configuration_string = ''.join(
            ['config={\n  ',
             ',\n  '.join(["'%s': %s" % (hp_name, repr(configuration[hp_name]))
                                         for hp_name in sorted(configuration)]),
             '}'])

        if len(self.dataset_properties_) > 0:
            dataset_properties_string = []
            dataset_properties_string.append('dataset_properties={')
            for i, item in enumerate(self.dataset_properties_.items()):
                if i != 0:
                    dataset_properties_string.append(',\n  ')
                else:
                    dataset_properties_string.append('\n  ')

                if isinstance(item[1], str):
                    dataset_properties_string.append("'%s': '%s'" % (item[0],
                                                                     item[1]))
                else:
                    dataset_properties_string.append("'%s': %s" % (item[0],
                                                                   item[1]))
            dataset_properties_string.append('}')
            dataset_properties_string = ''.join(dataset_properties_string)

            rval = '%s(%s,\n%s)' % (class_name, configuration,
                                    dataset_properties_string)
        else:
            rval = '%s(%s)' % (class_name, configuration_string)

        return rval

    def _get_pipeline(self):
        raise NotImplementedError()

    def _get_estimator_hyperparameter_name(self):
        raise NotImplementedError()
