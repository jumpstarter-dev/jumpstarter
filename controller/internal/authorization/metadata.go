package authorization

import (
	"context"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
)

var _ = ContextAttributesGetter(&MetadataAttributesGetter{})

type MetadataAttributesGetterConfig struct {
	NamespaceKey string
	ResourceKey  string
	NameKey      string
}

type MetadataAttributesGetter struct {
	config MetadataAttributesGetterConfig
}

func NewMetadataAttributesGetter(config MetadataAttributesGetterConfig) *MetadataAttributesGetter {
	return &MetadataAttributesGetter{
		config: config,
	}
}

func (b *MetadataAttributesGetter) ContextAttributes(
	ctx context.Context,
	userInfo user.Info,
) (authorizer.Attributes, error) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "missing metadata")
	}

	namespace, err := mdGet(md, b.config.NamespaceKey)
	if err != nil {
		return nil, err
	}

	resource, err := mdGet(md, b.config.ResourceKey)
	if err != nil {
		return nil, err
	}

	name, err := mdGet(md, b.config.NameKey)
	if err != nil {
		return nil, err
	}

	return authorizer.AttributesRecord{
		User:      userInfo,
		Namespace: namespace,
		Resource:  resource,
		Name:      name,
	}, nil
}

func mdGet(md metadata.MD, k string) (string, error) {
	v := md.Get(k)
	if len(v) < 1 {
		return "", status.Errorf(codes.InvalidArgument, "missing metadata: %s", k)
	}
	if len(v) > 1 {
		return "", status.Errorf(codes.InvalidArgument, "multiple metadata: %s", k)
	}
	return v[0], nil
}
